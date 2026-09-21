import type { GpuInstance } from "./types";

/**
 * Deliberately small catalogue for MVP decisioning.
 * Specifications must be revalidated against the selected Region before deployment.
 */
export const GPU_INSTANCES: GpuInstance[] = [
  {
    instanceType: "ml.g6.2xlarge",
    gpuName: "NVIDIA L4",
    gpuCount: 1,
    memoryPerGpuGb: 24,
    relativeCompute: 1,
    costTier: "$",
    generation: "G6"
  },
  {
    instanceType: "ml.g6e.2xlarge",
    gpuName: "NVIDIA L40S",
    gpuCount: 1,
    memoryPerGpuGb: 48,
    relativeCompute: 2.2,
    costTier: "$$",
    generation: "G6e"
  },
  {
    instanceType: "ml.g6e.12xlarge",
    gpuName: "NVIDIA L40S",
    gpuCount: 4,
    memoryPerGpuGb: 48,
    relativeCompute: 7.8,
    costTier: "$$$",
    generation: "G6e"
  },
  {
    instanceType: "ml.p4d.24xlarge",
    gpuName: "NVIDIA A100",
    gpuCount: 8,
    memoryPerGpuGb: 40,
    relativeCompute: 13,
    costTier: "$$$$",
    generation: "P4d"
  },
  {
    instanceType: "ml.p4de.24xlarge",
    gpuName: "NVIDIA A100",
    gpuCount: 8,
    memoryPerGpuGb: 80,
    relativeCompute: 16,
    costTier: "$$$$",
    generation: "P4de"
  },
  {
    instanceType: "ml.p5.48xlarge",
    gpuName: "NVIDIA H100",
    gpuCount: 8,
    memoryPerGpuGb: 80,
    relativeCompute: 30,
    costTier: "$$$$",
    generation: "P5"
  }
];
