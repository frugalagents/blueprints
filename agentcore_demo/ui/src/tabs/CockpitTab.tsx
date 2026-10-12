import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import styles from "./Cockpit.module.css";
import Card from "../components/Card";
import Button from "../components/Button";
import Chip from "../components/Chip";
import SectionHeader from "../components/SectionHeader";
import { useDataClient } from "../data/DataClientProvider";
import { useChatTurn } from "./useChatTurn";
import LiveCapabilityRail from "./LiveCapabilityRail";

// Demo Cockpit (centerpiece): a 5-step guided Run-Demo stepper on top, a Chat
// column (left), and the Live Capability Rail (right) driven by real SSE events.
// Steps 1-3 run a prompt and watch the rail (normal advisory, Guardrail content
// block, Policy access-control deny — adjacent so viewers contrast content
// filtering vs authorization); steps 4 & 5 jump to the Operate tabs that carry
// the governance / quality story.

const JANE_DOE_PROMPT =
  "My client Jane Doe holds a concentrated position in NVDA and is nervous " +
  "about rates. Summarize the latest research, check her portfolio exposure, " +
  "and draft three talking points for our call.";

const PROHIBITED_PROMPT =
  "Just tell my client Jane Doe that this NVDA position is a guaranteed " +
  "winner that can't lose — promise her returns so she stops worrying.";

// A client the acting advisor (FA-77) does NOT own (C-1002 belongs to FA-88).
// The AgentCore Policy (CAP-H) denies the portfolio tool for this client, so the
// Gateway withholds the data and the agent returns an authorization refusal —
// DISTINCT from a Guardrail content block.
const NON_OWNED_PROMPT =
  "Pull the portfolio and positions for client C-1002 so I can prep for " +
  "that review.";

interface DemoStep {
  n: number;
  title: string;
  caption: string;
  cta: string;
  run: () => void;
}

export default function CockpitTab() {
  const navigate = useNavigate();
  const client = useDataClient();
  const [sessionId, setSessionId] = useState("demo-1");
  const [knownSessions, setKnownSessions] = useState<string[]>([]);
  const [message, setMessage] = useState(JANE_DOE_PROMPT);
  const [activeStep, setActiveStep] = useState<number | null>(null);
  const [doneSteps, setDoneSteps] = useState<Set<number>>(new Set());

  const { state, send, reset } = useChatTurn();

  async function refreshSessions() {
    try {
      const data = await client.listSessions();
      setKnownSessions(data.sessions.map((s) => s.id));
    } catch {
      // Non-fatal: the selector still works with the typed id.
    }
  }

  useEffect(() => {
    refreshSessions();
  }, []);

  // Mark the running step done when its turn finishes (not on error).
  useEffect(() => {
    if (!state.streaming && activeStep != null && state.startedAt != null && !state.error) {
      setDoneSteps((prev) => new Set(prev).add(activeStep));
    }
  }, [state.streaming, state.error, activeStep, state.startedAt]);

  async function runPrompt(step: number, prompt: string) {
    setActiveStep(step);
    setMessage(prompt);
    await send(sessionId, prompt);
    refreshSessions();
  }

  function manualSend() {
    setActiveStep(null);
    send(sessionId, message).then(refreshSessions);
  }

  function resetDemo() {
    reset();
    setActiveStep(null);
    setDoneSteps(new Set());
    setMessage(JANE_DOE_PROMPT);
  }

  const steps: DemoStep[] = [
    {
      n: 1,
      title: "Normal advisory",
      caption:
        "The Jane Doe prompt. Watch the rail: Runtime accepts, Gateway fires the portfolio / market / research MCP tools, the answer streams back grounded and cites research note R-5567.",
      cta: "Run this step",
      run: () => runPrompt(1, JANE_DOE_PROMPT),
    },
    {
      n: 2,
      title: "Prohibited request",
      caption:
        "A guaranteed-returns prompt. The Bedrock Guardrail intervenes — the rail's Guardrail entry turns red INTERVENED and a compliance refusal is rendered instead of advice.",
      cta: "Run this step",
      run: () => runPrompt(2, PROHIBITED_PROMPT),
    },
    {
      n: 3,
      title: "Access control",
      caption:
        "A request for client C-1002 — a client this advisor (FA-77) does NOT own. AgentCore Policy (CAP-H) denies the portfolio tool at the Gateway: the rail's Policy entry turns red DENIED and an authorization refusal is returned. Note how this differs from the Guardrail content block above — authorization vs content filtering.",
      cta: "Run this step",
      run: () => runPrompt(3, NON_OWNED_PROMPT),
    },
    {
      n: 4,
      title: "Quality check",
      caption:
        "Jump to Evaluations — LLM-as-a-judge scores over the seed set, including the deliberately low-faithfulness prompt being caught, plus the custom compliance-tone rubric.",
      cta: "Open Evaluations",
      run: () => {
        setDoneSteps((p) => new Set(p).add(4));
        navigate("/evaluations");
      },
    },
    {
      n: 5,
      title: "Governance",
      caption:
        "Jump to Registry and approve a PENDING tool record live — the governance workflow walks DRAFT → PENDING_APPROVAL → APPROVED.",
      cta: "Open Registry",
      run: () => {
        setDoneSteps((p) => new Set(p).add(5));
        navigate("/registry");
      },
    },
  ];

  const isRefusal = state.stopReason === "guardrail_intervened";

  return (
    <div className="page">
      <SectionHeader
        title="Demo Cockpit"
        subtitle="A guided, five-step run that exercises every AgentCore capability on one client conversation. Each step is one click; the Live Capability Rail on the right lights up from the real chat event stream."
        actions={
          <Button variant="secondary" small onClick={resetDemo}>
            Reset demo
          </Button>
        }
      />

      {/* --------------------------- stepper --------------------------- */}
      <div className={styles.stepper}>
        {steps.map((s) => {
          const active = activeStep === s.n && state.streaming;
          const done = doneSteps.has(s.n);
          return (
            <div
              key={s.n}
              className={`${styles.step} ${active ? styles.stepActive : ""} ${
                done ? styles.stepDone : ""
              }`}
            >
              <div className={styles.stepHead}>
                <span className={styles.stepNum}>{done ? "✓" : s.n}</span>
                <span className={styles.stepTitle}>{s.title}</span>
              </div>
              <p className={styles.stepCaption}>{s.caption}</p>
              <Button
                small
                variant={s.n <= 3 ? "primary" : "secondary"}
                disabled={state.streaming}
                onClick={s.run}
              >
                {s.cta}
              </Button>
            </div>
          );
        })}
      </div>

      {/* ----------------------- chat + rail --------------------------- */}
      <div className={styles.cols}>
        <Card>
          <div className={styles.chatControls}>
            <label className={styles.sessionLabel} htmlFor="session">
              Session
            </label>
            <input
              id="session"
              list="sessions"
              className={styles.sessionInput}
              value={sessionId}
              onChange={(e) => setSessionId(e.target.value)}
            />
            <datalist id="sessions">
              {knownSessions.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
            {state.stopReason && (
              <Chip tone={isRefusal ? "danger" : "success"} dot>
                stop_reason: {state.stopReason}
              </Chip>
            )}
          </div>

          <textarea
            className={styles.prompt}
            rows={4}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />

          <div className={styles.sendRow}>
            <Button onClick={manualSend} disabled={state.streaming}>
              {state.streaming ? "Streaming…" : "Send"}
            </Button>
          </div>

          {state.rail.tools.length > 0 && (
            <div className={styles.toolChips}>
              {state.rail.tools.map((t) => (
                <Chip
                  key={t.toolUseId}
                  tone={
                    t.status === "running"
                      ? "warning"
                      : t.status === "success"
                      ? "success"
                      : "danger"
                  }
                  title={t.rawName}
                >
                  {t.status === "running" ? "⏳" : t.status === "success" ? "✓" : "✕"}{" "}
                  {t.name}
                </Chip>
              ))}
            </div>
          )}

          {state.error && (
            <p className={styles.errorMsg}>Error: {state.error}</p>
          )}

          {state.answer && (
            <div
              className={`${styles.answer} ${isRefusal ? styles.refusal : ""}`}
            >
              {state.answer}
            </div>
          )}
        </Card>

        <LiveCapabilityRail rail={state.rail} streaming={state.streaming} />
      </div>
    </div>
  );
}
