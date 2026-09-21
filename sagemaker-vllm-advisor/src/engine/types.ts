export type Precision = "BF16" | "FP16" | "FP8" | "INT8" | "INT4";

export interface AssessmentInput {
  modelId: string;
  parameterBillions: number;
  layers: number;
  hiddenSize: number;
  attentionHeads: number;
  kvHeads: number;
  precision: Precision;
  p50InputTokens: number;
  p95InputTokens: number;
  p50OutputTokens: number;
  p95OutputTokens: number;
  peakRequestsPerSecond: number;
  targetTtftSeconds: number;
  maxModelLength: number;
  region: string;
  availability: "single" | "multi-az";
  monthlyCostCeiling?: number;
  sharedPromptPrefix: boolean;
}

export interface GpuInstance {
  instanceType: string;
  gpuName: string;
  gpuCount: number;
  memoryPerGpuGb: number;
  relativeCompute: number;
  costTier: "$" | "$$" | "$$$" | "$$$$";
  generation: string;
}

export type EvidenceKind =
  | "input"
  | "calculated"
  | "heuristic"
  | "benchmark"
  | "aws";

export interface AgentTraceStep {
  phase: "Reason" | "Act" | "Observe";
  title: string;
  detail: string;
  evidence: EvidenceKind;
}

export interface VllmSetting {
  parameter: string;
  value: string;
  reason: string;
}

export interface Candidate {
  instance: GpuInstance;
  fit: "comfortable" | "tight" | "not-fit";
  tensorParallelSize: number;
  weightMemoryGb: number;
  runtimeOverheadGb: number;
  availableKvCacheGb: number;
  kvCachePerSequenceGb: number;
  estimatedConcurrentSequences: number;
  estimatedOutputTokensPerSecond: number;
  estimatedReplicas: number;
  vllmSettings: VllmSetting[];
  warnings: string[];
  score: number;
  liveAws?: {
    hourlyPriceUsd?: number | null;
    monthlyPriceUsd?: number | null;
    priceStatus: "verified" | "unavailable" | "error";
    priceSource?: string | null;
    endpointQuota?: number | null;
    quotaCode?: string | null;
    quotaStatus: "verified" | "not-found" | "error";
  } | null;
}

export interface AdvisorResult {
  input: AssessmentInput;
  weightMemoryGb: number;
  kvBytesPerToken: number;
  p95SequenceTokens: number;
  candidates: Candidate[];
  recommendation?: Candidate;
  lowerCostAlternative?: Candidate;
  lowerLatencyAlternative?: Candidate;
  trace: AgentTraceStep[];
  evidence?: Array<Record<string, unknown>>;
  liveAwsEnabled?: boolean;
}
