import type { AssessmentInput, Precision } from "../engine/types";

export interface HuggingFaceModelSummary {
  id: string;
  downloads: number;
  likes: number;
  gated: boolean | "auto" | "manual";
  pipelineTag?: string;
}

interface BackendModelMetadata {
  id: string;
  revision?: string | null;
  architecture: string;
  modelType: string;
  gated: boolean;
  parameterBillions?: number | null;
  layers?: number | null;
  hiddenSize?: number | null;
  attentionHeads?: number | null;
  kvHeads?: number | null;
  maxPositionEmbeddings?: number | null;
  precision?: Precision | null;
}

interface RawModelDetails {
  id: string;
  safetensors?: { total?: number };
}

interface RawModelConfig {
  architectures?: string[];
  model_type?: string;
  hidden_size?: number;
  n_embd?: number;
  num_hidden_layers?: number;
  n_layer?: number;
  num_attention_heads?: number;
  n_head?: number;
  num_key_value_heads?: number;
  max_position_embeddings?: number;
  n_positions?: number;
  torch_dtype?: string;
}

export interface LoadedModelMetadata {
  id: string;
  revision?: string;
  architecture: string;
  modelType: string;
  gated: boolean;
  patch: Partial<AssessmentInput>;
}

function precisionFromDtype(dtype?: string): Precision | undefined {
  const normalized = dtype?.toLowerCase();
  if (normalized === "bfloat16" || normalized === "bf16") return "BF16";
  if (normalized === "float16" || normalized === "fp16") return "FP16";
  if (normalized === "float8" || normalized === "fp8") return "FP8";
  return undefined;
}

export function metadataToAssessmentPatch(
  details: RawModelDetails,
  config: RawModelConfig
): Partial<AssessmentInput> {
  const attentionHeads = config.num_attention_heads ?? config.n_head;
  const contextLength =
    config.max_position_embeddings ?? config.n_positions;
  const precision = precisionFromDtype(config.torch_dtype);
  return {
    modelId: details.id,
    ...(details.safetensors?.total
      ? { parameterBillions: details.safetensors.total / 1_000_000_000 }
      : {}),
    ...(config.num_hidden_layers ?? config.n_layer
      ? { layers: (config.num_hidden_layers ?? config.n_layer)! }
      : {}),
    ...(config.hidden_size ?? config.n_embd
      ? { hiddenSize: (config.hidden_size ?? config.n_embd)! }
      : {}),
    ...(attentionHeads ? { attentionHeads } : {}),
    ...(config.num_key_value_heads
      ? { kvHeads: config.num_key_value_heads }
      : attentionHeads
        ? { kvHeads: attentionHeads }
        : {}),
    ...(precision ? { precision } : {}),
    ...(contextLength
      ? { maxModelLength: Math.min(contextLength, 8192) }
      : {})
  };
}

async function apiError(response: Response, fallback: string) {
  try {
    const body = (await response.json()) as { detail?: string };
    return body.detail ?? fallback;
  } catch {
    return fallback;
  }
}

export async function searchHuggingFaceModels(
  query: string,
  signal?: AbortSignal
): Promise<HuggingFaceModelSummary[]> {
  const trimmed = query.trim();
  if (trimmed.length < 2) return [];
  const response = await fetch(
    `/api/models/search?q=${encodeURIComponent(trimmed)}`,
    { signal }
  );
  if (!response.ok) {
    throw new Error(
      await apiError(response, `Hugging Face search failed (${response.status}).`)
    );
  }
  return response.json();
}

export async function loadHuggingFaceModel(
  modelId: string
): Promise<LoadedModelMetadata> {
  const trimmed = modelId.trim();
  if (!trimmed.includes("/")) {
    throw new Error("Enter a complete model ID such as Qwen/Qwen3-8B.");
  }
  const response = await fetch(
    `/api/models/resolve?model_id=${encodeURIComponent(trimmed)}`
  );
  if (!response.ok) {
    throw new Error(
      await apiError(
        response,
        `Model metadata could not be loaded (${response.status}).`
      )
    );
  }
  const model = (await response.json()) as BackendModelMetadata;
  const patch: Partial<AssessmentInput> = {
    modelId: model.id,
    ...(model.parameterBillions
      ? { parameterBillions: model.parameterBillions }
      : {}),
    ...(model.layers ? { layers: model.layers } : {}),
    ...(model.hiddenSize ? { hiddenSize: model.hiddenSize } : {}),
    ...(model.attentionHeads ? { attentionHeads: model.attentionHeads } : {}),
    ...(model.kvHeads ? { kvHeads: model.kvHeads } : {}),
    ...(model.precision ? { precision: model.precision } : {}),
    ...(model.maxPositionEmbeddings
      ? { maxModelLength: Math.min(model.maxPositionEmbeddings, 8192) }
      : {})
  };
  return {
    id: model.id,
    revision: model.revision ?? undefined,
    architecture: model.architecture,
    modelType: model.modelType,
    gated: model.gated,
    patch
  };
}
