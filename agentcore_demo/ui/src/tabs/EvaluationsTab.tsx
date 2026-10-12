import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import styles from "./Operate.module.css";
import Card from "../components/Card";
import Button from "../components/Button";
import Chip from "../components/Chip";
import TabHeader from "./TabHeader";
import { useDataClient } from "../data/DataClientProvider";
import type { EvalRunResponse, EvaluationsResponse } from "../types";

// Evaluations (CAP-G · Evaluation): LLM-as-a-judge score cards + a recharts
// trend + an on-demand batch run. Scores are the REAL emitted metric values
// (Bedrock-AgentCore/Evaluations CloudWatch); a metric with no samples renders
// a truthful empty card rather than a fabricated number. The custom
// compliance-tone rubric runs as part of the batch (it has no slot in the fixed
// 4-metric CloudWatch score shape) and is called out in the narration, not
// invented as a card.

const LINE_COLORS: Record<string, string> = {
  Faithfulness: "var(--cap-observability)",
  Harmfulness: "var(--danger)",
  Correctness: "var(--success)",
  ToolSelectionAccuracy: "var(--cap-memory)",
};

const METRICS = [
  "Faithfulness",
  "Harmfulness",
  "Correctness",
  "ToolSelectionAccuracy",
] as const;

export default function EvaluationsTab() {
  const client = useDataClient();
  const [data, setData] = useState<EvaluationsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<EvalRunResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    client
      .getEvaluations()
      .then(setData)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [client]);

  async function runBatch() {
    setBusy(true);
    setError(null);
    try {
      setRun(await client.runEvaluations());
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const hasSamples = (data?.score_cards ?? []).some((c) => c.sample_size > 0);
  const hasTrend = (data?.trend ?? []).length > 0;

  return (
    <div className="page">
      <TabHeader
        capability="evaluation"
        suffix="Evaluation"
        title="Evaluations"
        description="LLM-as-a-judge scores from the Bedrock-AgentCore Evaluations namespace — Faithfulness, Harmfulness, Correctness, Tool-selection accuracy — with a daily trend. The seed set deliberately includes a low-faithfulness prompt so you can see the judge catch it; a custom compliance-tone rubric also runs on each batch. Run batch starts an on-demand evaluation over the curated seed set."
        actions={
          <Button onClick={runBatch} disabled={busy}>
            {busy ? "Starting…" : "Run batch"}
          </Button>
        }
      />

      {error && <p className={styles.errorMsg}>{error}</p>}
      {run && (
        <div className={styles.metaLine}>
          <Chip tone="success" dot>
            {run.status}
          </Chip>
          <span>
            {run.message} (batch <span className={styles.mono}>{run.batch_id}</span>,{" "}
            {run.seed_set_size} seed prompts)
          </span>
        </div>
      )}

      <div className={styles.scoreGrid}>
        {data?.score_cards.map((c) => {
          const empty = c.sample_size === 0;
          return (
            <div key={c.metric} className={styles.scoreCard}>
              <div className={styles.scoreMetric}>{c.metric}</div>
              <div
                className={`${styles.scoreValue} ${
                  empty ? styles.scoreValueEmpty : ""
                }`}
              >
                {empty ? "—" : `${(c.score * 100).toFixed(0)}%`}
              </div>
              <div className={styles.scoreSample}>
                {empty ? "no samples in window" : `n=${c.sample_size}`}
              </div>
            </div>
          );
        })}
      </div>

      {!loading && !hasSamples && !error && (
        <p className={styles.emptyMsg}>
          No evaluator samples in the lookback window yet. Click Run batch to
          score the curated seed set, or run turns in the Demo Cockpit so online
          sampling collects scores.
        </p>
      )}

      {hasTrend && (
        <>
          <h3 className={styles.subhead}>Daily trend</h3>
          <Card>
            <div className={styles.chartWrap}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart
                  data={data?.trend}
                  margin={{ top: 8, right: 24, bottom: 8, left: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--slate-200)" />
                  <XAxis dataKey="date" fontSize={12} stroke="var(--slate-500)" />
                  <YAxis domain={[0, 1]} fontSize={12} stroke="var(--slate-500)" />
                  <Tooltip />
                  <Legend />
                  {METRICS.map((metric) => (
                    <Line
                      key={metric}
                      type="monotone"
                      dataKey={metric}
                      stroke={LINE_COLORS[metric]}
                      strokeWidth={2}
                      dot={false}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
