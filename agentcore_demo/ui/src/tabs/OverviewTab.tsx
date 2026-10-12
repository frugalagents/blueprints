import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import styles from "./Overview.module.css";
import Card from "../components/Card";
import Chip, { type ChipTone } from "../components/Chip";
import SectionHeader from "../components/SectionHeader";
import ArchitectureDiagram from "../components/ArchitectureDiagram";
import { useDataClient } from "../data/DataClientProvider";
import { useHealth, statusTone } from "../useHealth";
import { CAPABILITIES, type CapabilityKey } from "../capabilities";

// Overview (landing). Explains the use case, draws the architecture, and shows
// 8 live capability cards — each reads REAL status from the BFF and deep-links
// to its detail tab. Status is derived truthfully: loading / empty / error
// states are shown rather than fabricated data.

type CardStatus =
  | { phase: "loading" }
  | { phase: "error" }
  | { phase: "ready"; tone: ChipTone; chip: string; detail: string };

const HEALTH_TONE: Record<ReturnType<typeof statusTone>, ChipTone> = {
  ok: "success",
  warn: "warning",
  bad: "danger",
  idle: "neutral",
};

export default function OverviewTab() {
  const navigate = useNavigate();
  const client = useDataClient();
  const health = useHealth();

  // Operator-endpoint reads for the non-health capability cards.
  const [registry, setRegistry] = useState<CardStatus>({ phase: "loading" });
  const [memory, setMemory] = useState<CardStatus>({ phase: "loading" });
  const [traces, setTraces] = useState<CardStatus>({ phase: "loading" });
  const [evals, setEvals] = useState<CardStatus>({ phase: "loading" });
  const [guardrailLog, setGuardrailLog] = useState<CardStatus>({ phase: "loading" });
  const [policy, setPolicy] = useState<CardStatus>({ phase: "loading" });

  useEffect(() => {
    client
      .getRegistry()
      .then((d) => {
        const approved = d.records.filter((r) => r.governance_state === "APPROVED").length;
        setRegistry({
          phase: "ready",
          tone: d.records.length ? "success" : "neutral",
          chip: `${d.records.length} records`,
          detail: d.records.length
            ? `${approved} approved · ${d.records.length - approved} in workflow`
            : "No records returned",
        });
      })
      .catch(() => setRegistry({ phase: "error" }));

    client
      .listSessions()
      .then((d) => {
        const turns = d.sessions.reduce((n, s) => n + s.turn_count, 0);
        setMemory({
          phase: "ready",
          tone: d.sessions.length ? "success" : "neutral",
          chip: d.sessions.length ? "ACTIVE" : "EMPTY",
          detail: d.sessions.length
            ? `${d.sessions.length} session(s) · ${turns} turns persisted`
            : "No sessions yet — run a turn in the Cockpit",
        });
      })
      .catch(() => setMemory({ phase: "error" }));

    client
      .listTraces(50)
      .then((d) => {
        setTraces({
          phase: "ready",
          tone: d.traces.length ? "success" : "neutral",
          chip: `${d.traces.length} traces`,
          detail: d.traces.length
            ? "ADOT spans landing in CloudWatch GenAI Observability"
            : "No traces in the lookback window yet",
        });
      })
      .catch(() => setTraces({ phase: "error" }));

    client
      .getEvaluations()
      .then((d) => {
        const scored = d.score_cards.filter((c) => c.sample_size > 0);
        setEvals({
          phase: "ready",
          tone: scored.length ? "success" : "neutral",
          chip: scored.length ? `${scored.length}/4 scored` : "NO SAMPLES",
          detail: scored.length
            ? "LLM-as-a-judge evaluators scoring runs"
            : "No evaluator samples in the window yet",
        });
      })
      .catch(() => setEvals({ phase: "error" }));

    client
      .getGuardrails()
      .then((d) => {
        setGuardrailLog({
          phase: "ready",
          tone: "success",
          chip: `v${d.guardrail_version}`,
          detail: `${d.events.length} intervention event(s) in the window`,
        });
      })
      .catch(() => setGuardrailLog({ phase: "error" }));

    client
      .getPolicy()
      .then((d) => {
        const active =
          d.policy_status === "ACTIVE" && d.engine_status === "ACTIVE";
        setPolicy({
          phase: "ready",
          tone: active ? "success" : "neutral",
          chip: `${d.enforcement_mode} / ${d.engine_status}`,
          detail: `Acting advisor ${d.rule.acting_advisor} · ${d.rule.owned_clients.length} owned client(s)`,
        });
      })
      .catch(() => setPolicy({ phase: "error" }));
  }, [client]);

  // Health-derived statuses for Runtime / Gateway / Guardrail.
  const h = health.status === "ok" ? health.health : null;
  const healthCard = (value?: string, extra?: string): CardStatus => {
    if (health.status === "loading") return { phase: "loading" };
    if (health.status === "error") return { phase: "error" };
    const tone = HEALTH_TONE[statusTone(value)];
    return {
      phase: "ready",
      tone,
      chip: value ?? "unavailable",
      detail: extra ?? "",
    };
  };

  const statusByKey: Record<CapabilityKey, CardStatus> = {
    runtime: healthCard(h?.runtime, h?.model ? `model ${h.model}` : ""),
    gateway: healthCard(h?.gateway, "3 MCP tool targets (Portfolio · Market · Research)"),
    guardrail:
      guardrailLog.phase === "ready"
        ? { ...guardrailLog, tone: HEALTH_TONE[statusTone(h?.guardrail)] }
        : guardrailLog,
    policy,
    memory,
    registry,
    observability: traces,
    evaluation: evals,
  };

  const CARD_ORDER: CapabilityKey[] = [
    "runtime",
    "gateway",
    "guardrail",
    "policy",
    "memory",
    "registry",
    "observability",
    "evaluation",
  ];

  const DESC: Record<CapabilityKey, string> = {
    runtime: "Strands agent hosted on AgentCore Runtime (ARM64), streaming replies over HTTP/SSE.",
    gateway: "Three mocked APIs exposed as MCP tools over SigV4; the agent calls them per turn.",
    guardrail: "Bedrock Guardrail applied every turn — denied topics, PII, word policy, grounding.",
    policy: "Cedar authorization at the Gateway — an advisor's agent may read only that advisor's own clients.",
    memory: "Per-session conversation memory so the advisor keeps context across turns.",
    registry: "Agent + tools catalogued with a DRAFT → PENDING_APPROVAL → APPROVED workflow.",
    observability: "ADOT spans per turn — model + tool calls, latency, tokens — in CloudWatch.",
    evaluation: "LLM-as-a-judge scores (Faithfulness, Harmfulness, Correctness, Tool-selection).",
  };

  return (
    <div className="page">
      <SectionHeader
        title="Advisor Copilot"
        subtitle="A Financial Advisor copilot that turns a client, their portfolio, live market data, and internal research into grounded, compliant talking points — demonstrating all eight Amazon Bedrock AgentCore capabilities on one realistic use case."
      />
      <div className={styles.pillRow}>
        <Chip tone="info" dot>It advises, never trades</Chip>
        <Chip tone="neutral" dot>All data is synthetic</Chip>
        <Chip tone="gold" dot>Compliance guardrails always on</Chip>
      </div>

      <h2 className={styles.sectionTitle}>Architecture</h2>
      <Card>
        <ArchitectureDiagram />
        <p className={styles.archNote}>
          The React UI talks only to the FastAPI BFF; AWS credentials stay
          server-side. The BFF proxies chat to the AgentCore Runtime (SSE) and
          reads governance and quality data back via boto3. The Runtime calls
          Gateway MCP tools (SigV4), persists turns to Memory, and applies the
          Guardrail on every turn. Observability, Evaluation, and Registry form
          the governance &amp; quality plane. All of it is live — not slides.
        </p>
      </Card>

      <h2 className={styles.sectionTitle}>The 8 AgentCore capabilities (live)</h2>
      <div className={styles.grid}>
        {CARD_ORDER.map((key) => {
          const cap = CAPABILITIES[key];
          const st = statusByKey[key];
          return (
            <Card
              key={key}
              interactive
              onClick={() => navigate(cap.route)}
              className={styles.capCard}
            >
              <div className={styles.capHead}>
                <span
                  className={styles.capLetter}
                  style={{ background: cap.colorVar }}
                >
                  {cap.letter}
                </span>
                <span className={styles.capName}>{cap.label}</span>
                <span className={styles.capRole}>CAP-{cap.letter}</span>
              </div>
              <p className={styles.capDesc}>{DESC[key]}</p>
              <div className={styles.capFoot}>
                {st.phase === "loading" && <Chip tone="neutral">loading…</Chip>}
                {st.phase === "error" && <Chip tone="danger">unavailable</Chip>}
                {st.phase === "ready" && <Chip tone={st.tone} dot>{st.chip}</Chip>}
                <span className={styles.capLink}>View →</span>
              </div>
              {st.phase === "ready" && st.detail && (
                <p className={styles.capDesc} style={{ marginTop: -4, color: "var(--slate-500)", fontSize: "var(--fs-xs)" }}>
                  {st.detail}
                </p>
              )}
            </Card>
          );
        })}
      </div>

      <p className={styles.footnote}>
        Proof of concept — synthetic data only. Not investment advice.
      </p>
    </div>
  );
}
