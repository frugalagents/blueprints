import { describe, expect, it } from "vitest";
import { metadataToAssessmentPatch } from "./huggingface";

describe("Hugging Face metadata normalization", () => {
  it("maps a Qwen-style config into advisor inputs", () => {
    const patch = metadataToAssessmentPatch(
      {
        id: "Qwen/Qwen2.5-7B-Instruct",
        safetensors: { total: 7_615_616_512 }
      },
      {
        architectures: ["Qwen2ForCausalLM"],
        model_type: "qwen2",
        hidden_size: 3584,
        num_hidden_layers: 28,
        num_attention_heads: 28,
        num_key_value_heads: 4,
        max_position_embeddings: 32768,
        torch_dtype: "bfloat16"
      }
    );

    expect(patch).toMatchObject({
      modelId: "Qwen/Qwen2.5-7B-Instruct",
      layers: 28,
      hiddenSize: 3584,
      attentionHeads: 28,
      kvHeads: 4,
      precision: "BF16",
      maxModelLength: 8192
    });
    expect(patch.parameterBillions).toBeCloseTo(7.6156, 3);
  });

  it("supports GPT-style alternate config keys", () => {
    const patch = metadataToAssessmentPatch(
      { id: "org/model" },
      {
        n_embd: 2048,
        n_layer: 24,
        n_head: 16,
        n_positions: 4096,
        torch_dtype: "float16"
      }
    );
    expect(patch).toMatchObject({
      hiddenSize: 2048,
      layers: 24,
      attentionHeads: 16,
      kvHeads: 16,
      maxModelLength: 4096,
      precision: "FP16"
    });
  });
});
