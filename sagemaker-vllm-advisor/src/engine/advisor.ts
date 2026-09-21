import { GPU_INSTANCES } from "./catalog";
import type {
  AdvisorResult,
  AgentTraceStep,
  AssessmentInput,
  Candidate,
  GpuInstance,
  Precision,
  VllmSetting
} from "./types";

const PRECISION_BYTES: Record<Precision, number> = {
  BF16: 2,
  FP16: 2,
  FP8: 1,
  INT8: 1,
  INT4: 0.55
};

const COST_PENALTY: Record<GpuInstance["costTier"], number> = {
  $: 0,
  $$: 12,
  $$$: 25,
  $$$$: 42
};

const validParallelSizes = (gpuCount: number) =>
  [1, 2, 4, 8].filter((size) => size <= gpuCount && gpuCount % size === 0);

export function calculateWeightMemoryGb(
  parameterBillions: number,
  precision: Precision
) {
  return parameterBillions * PRECISION_BYTES[precision] * 1.04;
}

export function calculateKvBytesPerToken(input: AssessmentInput) {
  const kvRatio = input.kvHeads / input.attentionHeads;
  return (
    2 *
    input.layers *
    input.hiddenSize *
    kvRatio *
    PRECISION_BYTES[input.precision]
  );
}

function chooseTensorParallelSize(
  instance: GpuInstance,
  weightMemoryGb: number,
  runtimeOverheadGb: number
) {
  return validParallelSizes(instance.gpuCount).find((parallelSize) => {
    const usableMemory =
      instance.memoryPerGpuGb * parallelSize * 0.9;
    return weightMemoryGb + runtimeOverheadGb <= usableMemory;
  });
}

function buildVllmSettings(
  input: AssessmentInput,
  tensorParallelSize: number,
  maxSequences: number
): VllmSetting[] {
  const safeMaxSequences = Math.max(1, Math.min(maxSequences, 256));
  const batchTokens = Math.max(
    2048,
    Math.min(32768, input.p95InputTokens + input.p50OutputTokens * 4)
  );

  return [
    {
      parameter: "dtype",
      value: input.precision.toLowerCase(),
      reason: "Matches the selected model precision."
    },
    {
      parameter: "tensor_parallel_size",
      value: String(tensorParallelSize),
      reason: "Uses the smallest GPU group that fits weights and runtime reserve."
    },
    {
      parameter: "pipeline_parallel_size",
      value: "1",
      reason: "Avoids pipeline latency until a multi-node deployment is required."
    },
    {
      parameter: "gpu_memory_utilization",
      value: "0.90",
      reason: "Allocates most GPU memory while preserving an operational reserve."
    },
    {
      parameter: "max_model_len",
      value: String(input.maxModelLength),
      reason: "Enforces the workload context policy instead of the model maximum."
    },
    {
      parameter: "max_num_seqs",
      value: String(safeMaxSequences),
      reason: "Bounded by the estimated KV-cache capacity; benchmark before raising."
    },
    {
      parameter: "max_num_batched_tokens",
      value: String(batchTokens),
      reason: "Initial throughput/latency balance derived from the prompt distribution."
    },
    {
      parameter: "enable_prefix_caching",
      value: input.sharedPromptPrefix ? "true" : "false",
      reason: input.sharedPromptPrefix
        ? "The workload reports reusable prompt prefixes."
        : "No repeated long prefix has been declared."
    }
  ];
}

function evaluateCandidate(
  input: AssessmentInput,
  instance: GpuInstance,
  weightMemoryGb: number,
  kvBytesPerToken: number
): Candidate {
  const runtimeOverheadGb = Math.max(2.5, weightMemoryGb * 0.12);
  const tensorParallelSize = chooseTensorParallelSize(
    instance,
    weightMemoryGb,
    runtimeOverheadGb
  );

  if (!tensorParallelSize) {
    return {
      instance,
      fit: "not-fit",
      tensorParallelSize: instance.gpuCount,
      weightMemoryGb,
      runtimeOverheadGb,
      availableKvCacheGb: 0,
      kvCachePerSequenceGb: 0,
      estimatedConcurrentSequences: 0,
      estimatedOutputTokensPerSecond: 0,
      estimatedReplicas: 0,
      vllmSettings: [],
      warnings: ["Weights and runtime reserve do not fit this GPU topology."],
      score: -1000
    };
  }

  const usableMemoryGb =
    instance.memoryPerGpuGb * tensorParallelSize * 0.9;
  const availableKvCacheGb = Math.max(
    0,
    usableMemoryGb - weightMemoryGb - runtimeOverheadGb
  );
  const kvCachePerSequenceGb =
    (kvBytesPerToken * input.maxModelLength) / 1_000_000_000;
  const estimatedConcurrentSequences = Math.max(
    0,
    Math.floor(availableKvCacheGb / Math.max(kvCachePerSequenceGb, 0.01))
  );
  const memoryHeadroomRatio =
    availableKvCacheGb / Math.max(usableMemoryGb, 0.01);

  const fit =
    estimatedConcurrentSequences >= 4 && memoryHeadroomRatio >= 0.2
      ? "comfortable"
      : "tight";

  const modelScalePenalty = Math.max(1, input.parameterBillions / 8);
  const parallelEfficiency = 1 - Math.max(0, tensorParallelSize - 1) * 0.06;
  const estimatedOutputTokensPerSecond = Math.max(
    1,
    (instance.relativeCompute * 95 * parallelEfficiency) / modelScalePenalty
  );
  const peakOutputDemand =
    input.peakRequestsPerSecond * input.p50OutputTokens;
  const usableThroughput = estimatedOutputTokensPerSecond * 0.7;
  let estimatedReplicas = Math.max(
    1,
    Math.ceil(peakOutputDemand / Math.max(usableThroughput, 1))
  );
  if (input.availability === "multi-az") {
    estimatedReplicas = Math.max(2, estimatedReplicas);
  }

  const warnings: string[] = [];
  if (fit === "tight") {
    warnings.push(
      "Memory fit is tight; context length and concurrency require load validation."
    );
  }
  if (tensorParallelSize > 1) {
    warnings.push(
      `Model is sharded across ${tensorParallelSize} GPUs; benchmark communication overhead.`
    );
  }
  warnings.push(
    "Throughput and replica count are planning estimates until measured."
  );

  const score =
    100 -
    COST_PENALTY[instance.costTier] -
    (fit === "tight" ? 25 : 0) -
    (tensorParallelSize - 1) * 3 +
    Math.min(25, estimatedConcurrentSequences * 1.5) +
    Math.min(20, instance.relativeCompute / modelScalePenalty);

  return {
    instance,
    fit,
    tensorParallelSize,
    weightMemoryGb,
    runtimeOverheadGb,
    availableKvCacheGb,
    kvCachePerSequenceGb,
    estimatedConcurrentSequences,
    estimatedOutputTokensPerSecond,
    estimatedReplicas,
    vllmSettings: buildVllmSettings(
      input,
      tensorParallelSize,
      estimatedConcurrentSequences
    ),
    warnings,
    score
  };
}

export function runAdvisor(input: AssessmentInput): AdvisorResult {
  const trace: AgentTraceStep[] = [];
  const weightMemoryGb = calculateWeightMemoryGb(
    input.parameterBillions,
    input.precision
  );
  const kvBytesPerToken = calculateKvBytesPerToken(input);
  const p95SequenceTokens = input.p95InputTokens + input.p95OutputTokens;

  trace.push({
    phase: "Reason",
    title: "Establish the feasibility gate",
    detail:
      "First determine whether model weights, runtime reserve, and KV cache can fit the available GPU topology.",
    evidence: "calculated"
  });
  trace.push({
    phase: "Act",
    title: "Run model-memory calculator",
    detail: `${input.parameterBillions.toFixed(2)}B parameters at ${input.precision} require approximately ${weightMemoryGb.toFixed(1)} GB including serialization allowance.`,
    evidence: "calculated"
  });
  trace.push({
    phase: "Observe",
    title: "Context pressure identified",
    detail: `The workload's P95 sequence is ${p95SequenceTokens.toLocaleString()} tokens under a ${input.maxModelLength.toLocaleString()} token policy.`,
    evidence: "input"
  });

  const candidates = GPU_INSTANCES.map((instance) =>
    evaluateCandidate(input, instance, weightMemoryGb, kvBytesPerToken)
  )
    .filter((candidate) => candidate.fit !== "not-fit")
    .sort((a, b) => b.score - a.score);

  trace.push({
    phase: "Act",
    title: "Evaluate SageMaker candidates",
    detail: `${GPU_INSTANCES.length} catalogue configurations were checked; ${candidates.length} passed the model-fit gate.`,
    evidence: "calculated"
  });

  const recommendation = candidates[0];
  const lowerCostAlternative = [...candidates].sort(
    (a, b) =>
      COST_PENALTY[a.instance.costTier] -
        COST_PENALTY[b.instance.costTier] ||
      b.score - a.score
  )[0];
  const lowerLatencyAlternative = [...candidates].sort(
    (a, b) =>
      b.estimatedOutputTokensPerSecond -
      a.estimatedOutputTokensPerSecond
  )[0];

  if (recommendation) {
    trace.push({
      phase: "Observe",
      title: "Balanced baseline selected",
      detail: `${recommendation.instance.instanceType} has the strongest current balance of memory headroom, parallelism, relative cost, and estimated capacity.`,
      evidence: "heuristic"
    });
    trace.push({
      phase: "Reason",
      title: "Require a benchmark before production",
      detail:
        "The instance ranking is useful for shortlisting, but TTFT, inter-token latency, throughput, and replica count remain benchmark-required.",
      evidence: "benchmark"
    });
  }

  return {
    input,
    weightMemoryGb,
    kvBytesPerToken,
    p95SequenceTokens,
    candidates,
    recommendation,
    lowerCostAlternative,
    lowerLatencyAlternative,
    trace
  };
}
