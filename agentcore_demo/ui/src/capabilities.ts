// The 8 AgentCore capabilities (design.md §2 capability map + Policy CAP-H) as a
// single source
// of truth: letter, short label, the Operate tab (route) that demonstrates each,
// and the CSS custom-property name for its accent color. Used by the Overview
// capability cards, the capability badges, and the Live Capability Rail so the
// framing is consistent across the whole UI.

export type CapabilityKey =
  | "runtime"
  | "gateway"
  | "guardrail"
  | "policy"
  | "memory"
  | "registry"
  | "observability"
  | "evaluation";

export interface Capability {
  key: CapabilityKey;
  letter: string; // A–H, matching design.md capability map (H = Policy)
  label: string;
  /** Route path in the Operate zone that demonstrates this capability. */
  route: string;
  /** CSS variable (var(--cap-*)) for this capability's accent color. */
  colorVar: string;
}

export const CAPABILITIES: Record<CapabilityKey, Capability> = {
  runtime: {
    key: "runtime",
    letter: "D",
    label: "Runtime",
    route: "/cockpit",
    colorVar: "var(--cap-runtime)",
  },
  gateway: {
    key: "gateway",
    letter: "B",
    label: "Gateway",
    route: "/cockpit",
    colorVar: "var(--cap-gateway)",
  },
  guardrail: {
    key: "guardrail",
    letter: "A",
    label: "Guardrail",
    route: "/guardrails",
    colorVar: "var(--cap-guardrail)",
  },
  policy: {
    key: "policy",
    letter: "H",
    label: "Policy",
    route: "/policy",
    colorVar: "var(--cap-policy)",
  },
  memory: {
    key: "memory",
    letter: "C",
    label: "Memory",
    route: "/memory",
    colorVar: "var(--cap-memory)",
  },
  registry: {
    key: "registry",
    letter: "E",
    label: "Registry",
    route: "/registry",
    colorVar: "var(--cap-registry)",
  },
  observability: {
    key: "observability",
    letter: "F",
    label: "Observability",
    route: "/traces",
    colorVar: "var(--cap-observability)",
  },
  evaluation: {
    key: "evaluation",
    letter: "G",
    label: "Evaluation",
    route: "/evaluations",
    colorVar: "var(--cap-evaluation)",
  },
};
