from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class Precision(StrEnum):
    BF16 = "BF16"
    FP16 = "FP16"
    FP8 = "FP8"
    INT8 = "INT8"
    INT4 = "INT4"


class AssessmentInput(ApiModel):
    model_id: str = Field(min_length=3, max_length=300)
    parameter_billions: float = Field(gt=0, le=5000)
    layers: int = Field(gt=0, le=1000)
    hidden_size: int = Field(gt=0, le=1_000_000)
    attention_heads: int = Field(gt=0, le=4096)
    kv_heads: int = Field(gt=0, le=4096)
    precision: Precision
    p50_input_tokens: int = Field(ge=1)
    p95_input_tokens: int = Field(ge=1)
    p50_output_tokens: int = Field(ge=1)
    p95_output_tokens: int = Field(ge=1)
    peak_requests_per_second: float = Field(gt=0)
    target_ttft_seconds: float = Field(gt=0)
    max_model_length: int = Field(ge=128)
    region: str = Field(pattern=r"^[a-z]{2}(-gov)?-[a-z]+-\d$")
    availability: Literal["single", "multi-az"]
    monthly_cost_ceiling: float | None = Field(default=None, gt=0)
    shared_prompt_prefix: bool = False


class EvidenceRecord(ApiModel):
    name: str
    value: Any
    evidence_type: Literal[
        "customer_input",
        "calculated",
        "aws_api",
        "huggingface_api",
        "planning_heuristic",
        "benchmark_required",
    ]
    source: str
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    region: str | None = None
    source_reference: str | None = None


class GpuInstance(ApiModel):
    instance_type: str
    gpu_name: str
    gpu_count: int
    memory_per_gpu_gb: float
    relative_compute: float
    generation: str


class VllmSetting(ApiModel):
    parameter: str
    value: str
    reason: str


class LiveAwsEvidence(ApiModel):
    hourly_price_usd: float | None = None
    monthly_price_usd: float | None = None
    price_status: Literal["verified", "unavailable", "error"] = "unavailable"
    price_source: str | None = None
    endpoint_quota: float | None = None
    quota_code: str | None = None
    quota_status: Literal["verified", "not-found", "error"] = "not-found"


class Candidate(ApiModel):
    instance: GpuInstance
    fit: Literal["comfortable", "tight", "not-fit"]
    tensor_parallel_size: int
    weight_memory_gb: float
    runtime_overhead_gb: float
    available_kv_cache_gb: float
    kv_cache_per_sequence_gb: float
    estimated_concurrent_sequences: int
    estimated_output_tokens_per_second: float
    estimated_replicas: int
    vllm_settings: list[VllmSetting]
    warnings: list[str]
    score: float
    live_aws: LiveAwsEvidence | None = None


class AgentTraceStep(ApiModel):
    phase: Literal["Reason", "Act", "Observe"]
    title: str
    detail: str
    evidence: Literal["input", "calculated", "heuristic", "benchmark", "aws"]


class AdvisorResult(ApiModel):
    input: AssessmentInput
    weight_memory_gb: float
    kv_bytes_per_token: float
    p95_sequence_tokens: int
    candidates: list[Candidate]
    recommendation: Candidate | None = None
    lower_cost_alternative: Candidate | None = None
    lower_latency_alternative: Candidate | None = None
    trace: list[AgentTraceStep]
    evidence: list[EvidenceRecord]
    live_aws_enabled: bool


class ModelSearchResult(ApiModel):
    id: str
    downloads: int = 0
    likes: int = 0
    gated: bool | str = False
    pipeline_tag: str | None = None


class ModelMetadata(ApiModel):
    id: str
    revision: str | None = None
    architecture: str
    model_type: str
    gated: bool
    parameter_billions: float | None = None
    layers: int | None = None
    hidden_size: int | None = None
    attention_heads: int | None = None
    kv_heads: int | None = None
    max_position_embeddings: int | None = None
    precision: Precision | None = None
    evidence: list[EvidenceRecord]


class DeploymentPlanRequest(ApiModel):
    assessment: AssessmentInput
    candidate: Candidate
    model_revision: str | None = None
    container_image_digest: str = "UNRESOLVED"
    maximum_test_cost_usd: float = Field(default=25, gt=0)
    expires_in_hours: int = Field(default=4, ge=1, le=24)


class DeploymentPlan(ApiModel):
    plan_id: str
    plan_hash: str
    assessment: AssessmentInput
    candidate: Candidate
    model_revision: str | None
    container_image_digest: str
    maximum_test_cost_usd: float
    cleanup_deadline: datetime
    executable: bool = False
    blocked_reason: str = (
        "AWS write operations are disabled until this exact plan hash receives approval."
    )


class AgentQuestion(ApiModel):
    question: str = Field(min_length=2, max_length=4000)
    assessment: AssessmentInput | None = None


class AgentAnswer(ApiModel):
    answer: str
    model_id: str
    trace: list[AgentTraceStep]
    tools_used: list[str]
