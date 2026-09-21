import { describe, expect, it } from "vitest";
import {
  calculateKvBytesPerToken,
  calculateWeightMemoryGb,
  runAdvisor
} from "./advisor";
import type { AssessmentInput } from "./types";

const input: AssessmentInput = {
  modelId: "meta-llama/Llama-3.1-8B-Instruct",
  parameterBillions: 8.03,
  layers: 32,
  hiddenSize: 4096,
  attentionHeads: 32,
  kvHeads: 8,
  precision: "BF16",
  p50InputTokens: 1500,
  p95InputTokens: 6000,
  p50OutputTokens: 250,
  p95OutputTokens: 800,
  peakRequestsPerSecond: 3,
  targetTtftSeconds: 1.5,
  maxModelLength: 8192,
  region: "us-east-1",
  availability: "multi-az",
  sharedPromptPrefix: true
};

describe("advisor calculations", () => {
  it("calculates model weight memory with serialization allowance", () => {
    expect(calculateWeightMemoryGb(8.03, "BF16")).toBeCloseTo(16.7024);
  });

  it("accounts for grouped-query attention in KV cache", () => {
    expect(calculateKvBytesPerToken(input)).toBe(131072);
  });

  it("returns ranked viable candidates with vLLM settings", () => {
    const result = runAdvisor(input);
    expect(result.p95SequenceTokens).toBe(6800);
    expect(result.candidates.length).toBeGreaterThan(0);
    expect(result.recommendation?.vllmSettings).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ parameter: "max_model_len", value: "8192" }),
        expect.objectContaining({
          parameter: "enable_prefix_caching",
          value: "true"
        })
      ])
    );
    expect(result.recommendation?.estimatedReplicas).toBeGreaterThanOrEqual(2);
  });

  it("uses additional GPUs for a larger BF16 model", () => {
    const result = runAdvisor({
      ...input,
      modelId: "large-model",
      parameterBillions: 70,
      layers: 80,
      hiddenSize: 8192,
      attentionHeads: 64,
      kvHeads: 8
    });
    expect(result.candidates.length).toBeGreaterThan(0);
    expect(
      result.candidates.every((candidate) => candidate.tensorParallelSize > 1)
    ).toBe(true);
  });
});
