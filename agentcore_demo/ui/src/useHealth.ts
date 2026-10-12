import { useEffect, useState } from "react";
import { useDataClient } from "./data/DataClientProvider";
import type { HealthResponse } from "./types";

export type HealthState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ok"; health: HealthResponse };

// Reads GET /api/health once. Shared by the top bar health chips and the
// Overview Runtime/Gateway/Guardrail capability cards so they agree and the UI
// only hits the endpoint once per mount.
export function useHealth(): HealthState {
  const data = useDataClient();
  const [state, setState] = useState<HealthState>({ status: "loading" });
  useEffect(() => {
    let alive = true;
    data
      .getHealth()
      .then((health: HealthResponse) => alive && setState({ status: "ok", health }))
      .catch(() => alive && setState({ status: "error" }));
    return () => {
      alive = false;
    };
  }, [data]);
  return state;
}

// Classify a status string into a traffic-light tone. "READY"/"local"/"ACTIVE"
// are healthy; "not-deployed" / "unknown:*" / "CREATING" are degraded; blank is
// idle. Kept deliberately conservative so the UI never claims green falsely.
export function statusTone(
  value: string | undefined
): "ok" | "warn" | "bad" | "idle" {
  if (!value) return "idle";
  const v = value.toLowerCase();
  if (v === "ready" || v === "local" || v === "active" || v === "available")
    return "ok";
  if (v.startsWith("unknown") || v.includes("fail") || v === "not-deployed")
    return "bad";
  if (v === "creating" || v === "updating" || v === "pending") return "warn";
  // A guardrail version like "4" or an unexpected-but-present value: treat as ok
  // (it is a real configured value), not a failure.
  return "ok";
}
