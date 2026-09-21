from __future__ import annotations

import math

from backend.domain.models import (
    AdvisorResult,
    AgentTraceStep,
    AssessmentInput,
    Candidate,
    EvidenceRecord,
    GpuInstance,
    Precision,
    VllmSetting,
)

PRECISION_BYTES: dict[Precision, float] = {
    Precision.BF16: 2,
    Precision.FP16: 2,
    Precision.FP8: 1,
    Precision.INT8: 1,
    Precision.INT4: 0.55,
}

GPU_INSTANCES = [
    GpuInstance(
        instance_type="ml.g6.2xlarge",
        gpu_name="NVIDIA L4",
        gpu_count=1,
        memory_per_gpu_gb=24,
        relative_compute=1,
        generation="G6",
    ),
    GpuInstance(
        instance_type="ml.g6e.2xlarge",
        gpu_name="NVIDIA L40S",
        gpu_count=1,
        memory_per_gpu_gb=48,
        relative_compute=2.2,
        generation="G6e",
    ),
    GpuInstance(
        instance_type="ml.g6e.12xlarge",
        gpu_name="NVIDIA L40S",
        gpu_count=4,
        memory_per_gpu_gb=48,
        relative_compute=7.8,
        generation="G6e",
    ),
    GpuInstance(
        instance_type="ml.p4d.24xlarge",
        gpu_name="NVIDIA A100",
        gpu_count=8,
        memory_per_gpu_gb=40,
        relative_compute=13,
        generation="P4d",
    ),
    GpuInstance(
        instance_type="ml.p4de.24xlarge",
        gpu_name="NVIDIA A100",
        gpu_count=8,
        memory_per_gpu_gb=80,
        relative_compute=16,
        generation="P4de",
    ),
    GpuInstance(
        instance_type="ml.p5.48xlarge",
        gpu_name="NVIDIA H100",
        gpu_count=8,
        memory_per_gpu_gb=80,
        relative_compute=30,
        generation="P5",
    ),
]

COST_PENALTY = {
    "ml.g6.2xlarge": 0,
    "ml.g6e.2xlarge": 12,
    "ml.g6e.12xlarge": 25,
    "ml.p4d.24xlarge": 42,
    "ml.p4de.24xlarge": 42,
    "ml.p5.48xlarge": 42,
}


def calculate_weight_memory_gb(parameter_billions: float, precision: Precision) -> float:
    return parameter_billions * PRECISION_BYTES[precision] * 1.04


def calculate_kv_bytes_per_token(value: AssessmentInput) -> float:
    kv_ratio = value.kv_heads / value.attention_heads
    return (
        2
        * value.layers
        * value.hidden_size
        * kv_ratio
        * PRECISION_BYTES[value.precision]
    )


def choose_tensor_parallel_size(
    instance: GpuInstance, weight_memory_gb: float, runtime_overhead_gb: float
) -> int | None:
    valid_sizes = [
        size
        for size in (1, 2, 4, 8)
        if size <= instance.gpu_count and instance.gpu_count % size == 0
    ]
    for size in valid_sizes:
        usable_memory = instance.memory_per_gpu_gb * size * 0.9
        if weight_memory_gb + runtime_overhead_gb <= usable_memory:
            return size
    return None


def build_vllm_settings(
    value: AssessmentInput, tensor_parallel_size: int, max_sequences: int
) -> list[VllmSetting]:
    safe_max_sequences = max(1, min(max_sequences, 256))
    batch_tokens = max(
        2048, min(32768, value.p95_input_tokens + value.p50_output_tokens * 4)
    )
    return [
        VllmSetting(
            parameter="dtype",
            value=value.precision.value.lower(),
            reason="Matches the selected model precision.",
        ),
        VllmSetting(
            parameter="tensor_parallel_size",
            value=str(tensor_parallel_size),
            reason="Uses the smallest GPU group that fits weights and runtime reserve.",
        ),
        VllmSetting(
            parameter="pipeline_parallel_size",
            value="1",
            reason="Avoids pipeline latency until a multi-node deployment is required.",
        ),
        VllmSetting(
            parameter="gpu_memory_utilization",
            value="0.90",
            reason="Allocates most GPU memory while preserving an operational reserve.",
        ),
        VllmSetting(
            parameter="max_model_len",
            value=str(value.max_model_length),
            reason="Enforces the workload context policy instead of the model maximum.",
        ),
        VllmSetting(
            parameter="max_num_seqs",
            value=str(safe_max_sequences),
            reason="Bounded by estimated KV-cache capacity; benchmark before raising.",
        ),
        VllmSetting(
            parameter="max_num_batched_tokens",
            value=str(batch_tokens),
            reason="Initial throughput/latency balance derived from prompt distribution.",
        ),
        VllmSetting(
            parameter="enable_prefix_caching",
            value=str(value.shared_prompt_prefix).lower(),
            reason=(
                "The workload reports reusable prompt prefixes."
                if value.shared_prompt_prefix
                else "No repeated long prefix has been declared."
            ),
        ),
    ]


def evaluate_candidate(
    value: AssessmentInput,
    instance: GpuInstance,
    weight_memory_gb: float,
    kv_bytes_per_token: float,
) -> Candidate:
    runtime_overhead_gb = max(2.5, weight_memory_gb * 0.12)
    tensor_parallel_size = choose_tensor_parallel_size(
        instance, weight_memory_gb, runtime_overhead_gb
    )
    if tensor_parallel_size is None:
        return Candidate(
            instance=instance,
            fit="not-fit",
            tensor_parallel_size=instance.gpu_count,
            weight_memory_gb=weight_memory_gb,
            runtime_overhead_gb=runtime_overhead_gb,
            available_kv_cache_gb=0,
            kv_cache_per_sequence_gb=0,
            estimated_concurrent_sequences=0,
            estimated_output_tokens_per_second=0,
            estimated_replicas=0,
            vllm_settings=[],
            warnings=["Weights and runtime reserve do not fit this GPU topology."],
            score=-1000,
        )

    usable_memory_gb = instance.memory_per_gpu_gb * tensor_parallel_size * 0.9
    available_kv_cache_gb = max(
        0, usable_memory_gb - weight_memory_gb - runtime_overhead_gb
    )
    kv_cache_per_sequence_gb = (
        kv_bytes_per_token * value.max_model_length / 1_000_000_000
    )
    estimated_concurrent_sequences = max(
        0, math.floor(available_kv_cache_gb / max(kv_cache_per_sequence_gb, 0.01))
    )
    memory_headroom_ratio = available_kv_cache_gb / max(usable_memory_gb, 0.01)
    fit = (
        "comfortable"
        if estimated_concurrent_sequences >= 4 and memory_headroom_ratio >= 0.2
        else "tight"
    )

    model_scale_penalty = max(1, value.parameter_billions / 8)
    parallel_efficiency = 1 - max(0, tensor_parallel_size - 1) * 0.06
    estimated_output_tokens_per_second = max(
        1,
        instance.relative_compute
        * 95
        * parallel_efficiency
        / model_scale_penalty,
    )
    peak_output_demand = value.peak_requests_per_second * value.p50_output_tokens
    usable_throughput = estimated_output_tokens_per_second * 0.7
    estimated_replicas = max(
        1, math.ceil(peak_output_demand / max(usable_throughput, 1))
    )
    if value.availability == "multi-az":
        estimated_replicas = max(2, estimated_replicas)

    warnings = []
    if fit == "tight":
        warnings.append(
            "Memory fit is tight; context length and concurrency require load validation."
        )
    if tensor_parallel_size > 1:
        warnings.append(
            f"Model is sharded across {tensor_parallel_size} GPUs; benchmark communication overhead."
        )
    warnings.append("Throughput and replica count are planning estimates until measured.")

    score = (
        100
        - COST_PENALTY[instance.instance_type]
        - (25 if fit == "tight" else 0)
        - (tensor_parallel_size - 1) * 3
        + min(25, estimated_concurrent_sequences * 1.5)
        + min(20, instance.relative_compute / model_scale_penalty)
    )

    return Candidate(
        instance=instance,
        fit=fit,
        tensor_parallel_size=tensor_parallel_size,
        weight_memory_gb=weight_memory_gb,
        runtime_overhead_gb=runtime_overhead_gb,
        available_kv_cache_gb=available_kv_cache_gb,
        kv_cache_per_sequence_gb=kv_cache_per_sequence_gb,
        estimated_concurrent_sequences=estimated_concurrent_sequences,
        estimated_output_tokens_per_second=estimated_output_tokens_per_second,
        estimated_replicas=estimated_replicas,
        vllm_settings=build_vllm_settings(
            value, tensor_parallel_size, estimated_concurrent_sequences
        ),
        warnings=warnings,
        score=score,
    )


def analyze(value: AssessmentInput) -> AdvisorResult:
    weight_memory_gb = calculate_weight_memory_gb(
        value.parameter_billions, value.precision
    )
    kv_bytes_per_token = calculate_kv_bytes_per_token(value)
    p95_sequence_tokens = value.p95_input_tokens + value.p95_output_tokens

    candidates = sorted(
        [
            evaluate_candidate(
                value, instance, weight_memory_gb, kv_bytes_per_token
            )
            for instance in GPU_INSTANCES
        ],
        key=lambda candidate: candidate.score,
        reverse=True,
    )
    candidates = [candidate for candidate in candidates if candidate.fit != "not-fit"]
    recommendation = candidates[0] if candidates else None
    lower_cost = (
        min(
            candidates,
            key=lambda item: (
                COST_PENALTY[item.instance.instance_type],
                -item.score,
            ),
        )
        if candidates
        else None
    )
    lower_latency = (
        max(candidates, key=lambda item: item.estimated_output_tokens_per_second)
        if candidates
        else None
    )

    trace = [
        AgentTraceStep(
            phase="Reason",
            title="Establish the feasibility gate",
            detail=(
                "Determine whether weights, runtime reserve, and KV cache fit "
                "the available GPU topology."
            ),
            evidence="calculated",
        ),
        AgentTraceStep(
            phase="Act",
            title="Run model-memory calculator",
            detail=(
                f"{value.parameter_billions:.2f}B parameters at {value.precision.value} "
                f"require approximately {weight_memory_gb:.1f} GB."
            ),
            evidence="calculated",
        ),
        AgentTraceStep(
            phase="Observe",
            title="Context pressure identified",
            detail=(
                f"P95 sequence is {p95_sequence_tokens:,} tokens under a "
                f"{value.max_model_length:,}-token policy."
            ),
            evidence="input",
        ),
        AgentTraceStep(
            phase="Act",
            title="Evaluate SageMaker candidates",
            detail=(
                f"{len(GPU_INSTANCES)} catalogue configurations checked; "
                f"{len(candidates)} passed the model-fit gate."
            ),
            evidence="calculated",
        ),
    ]
    if recommendation:
        trace.extend(
            [
                AgentTraceStep(
                    phase="Observe",
                    title="Balanced baseline selected",
                    detail=(
                        f"{recommendation.instance.instance_type} currently has the "
                        "strongest balance of memory, parallelism, relative cost, and capacity."
                    ),
                    evidence="heuristic",
                ),
                AgentTraceStep(
                    phase="Reason",
                    title="Require a benchmark before production",
                    detail=(
                        "TTFT, inter-token latency, throughput, and replica count "
                        "remain benchmark-required."
                    ),
                    evidence="benchmark",
                ),
            ]
        )

    evidence = [
        EvidenceRecord(
            name="weight_memory_gb",
            value=round(weight_memory_gb, 4),
            evidence_type="calculated",
            source="deterministic:model-weight-memory:v1",
        ),
        EvidenceRecord(
            name="kv_bytes_per_token",
            value=kv_bytes_per_token,
            evidence_type="calculated",
            source="deterministic:kv-cache:v1",
        ),
        EvidenceRecord(
            name="replica_count",
            value=recommendation.estimated_replicas if recommendation else None,
            evidence_type="planning_heuristic",
            source="heuristic:relative-throughput:v1",
        ),
    ]

    return AdvisorResult(
        input=value,
        weight_memory_gb=weight_memory_gb,
        kv_bytes_per_token=kv_bytes_per_token,
        p95_sequence_tokens=p95_sequence_tokens,
        candidates=candidates,
        recommendation=recommendation,
        lower_cost_alternative=lower_cost,
        lower_latency_alternative=lower_latency,
        trace=trace,
        evidence=evidence,
        live_aws_enabled=False,
    )
