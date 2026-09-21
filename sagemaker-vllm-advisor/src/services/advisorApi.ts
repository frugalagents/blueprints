import type {
  AdvisorResult,
  AgentTraceStep,
  AssessmentInput,
  Candidate
} from "../engine/types";

export interface AgentAnswer {
  answer: string;
  modelId: string;
  trace: AgentTraceStep[];
  toolsUsed: string[];
}

export interface DeploymentPlan {
  planId: string;
  planHash: string;
  cleanupDeadline: string;
  executable: false;
  blockedReason: string;
  maximumTestCostUsd: number;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try {
      const payload = (await response.json()) as { detail?: string };
      detail = payload.detail ?? detail;
    } catch {
      // Keep the status-based error.
    }
    throw new Error(detail);
  }
  return response.json();
}

export function analyzeAssessment(input: AssessmentInput) {
  return postJson<AdvisorResult>("/api/assessments/analyze?live_aws=true", input);
}

export function askAdvisor(question: string, assessment: AssessmentInput) {
  return postJson<AgentAnswer>("/api/agent/ask", { question, assessment });
}

export function createDeploymentPlan(
  assessment: AssessmentInput,
  candidate: Candidate,
  modelRevision?: string
) {
  return postJson<DeploymentPlan>("/api/deployment-plans", {
    assessment,
    candidate,
    modelRevision,
    containerImageDigest: "UNRESOLVED",
    maximumTestCostUsd: 25,
    expiresInHours: 4
  });
}
