import { useEffect, useState } from "react";
import styles from "./Operate.module.css";
import Card from "../components/Card";
import Chip, { type ChipTone } from "../components/Chip";
import TabHeader from "./TabHeader";
import { useDataClient } from "../data/DataClientProvider";
import type { GuardrailListResponse } from "../types";

// Guardrails (CAP-A · Policy/Guardrails): the real Bedrock Guardrail
// intervention log from CloudWatch AWS/Bedrock/Guardrails metrics. No client
// text / PII is read — metrics carry counts + policy types only.

const ACTION_TONE: Record<string, ChipTone> = {
  BLOCKED: "danger",
  MASKED: "warning",
  FLAGGED: "info",
};

export default function GuardrailsTab() {
  const client = useDataClient();
  const [data, setData] = useState<GuardrailListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    client
      .getGuardrails()
      .then(setData)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [client]);

  return (
    <div className="page">
      <TabHeader
        capability="guardrail"
        suffix="Policy / Guardrails"
        title="Guardrails"
        description="Real Bedrock Guardrail interventions — denied prohibited advice, masked PII, blocked words, grounding flags. Counts come from CloudWatch metrics; no client text is ever read."
      />

      {error && <p className={styles.errorMsg}>{error}</p>}

      {data && (
        <div className={styles.metaLine}>
          <span>Guardrail version</span>
          <Chip tone="gold">v{data.guardrail_version}</Chip>
          <span>·</span>
          <span>{data.events.length} intervention event(s)</span>
        </div>
      )}

      <Card pad={false} style={{ overflow: "hidden" }}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Rule type</th>
              <th>Policy detail (PII-free)</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {data?.events.map((ev, i) => (
              <tr key={i}>
                <td className={styles.mono}>{ev.timestamp}</td>
                <td>
                  <Chip tone="neutral">{ev.rule_type}</Chip>
                </td>
                <td>{ev.masked_snippet}</td>
                <td>
                  <Chip tone={ACTION_TONE[ev.action] ?? "neutral"} dot>
                    {ev.action}
                  </Chip>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && data?.events.length === 0 && !error && (
          <p className={styles.emptyMsg} style={{ padding: "0 12px 12px" }}>
            No interventions recorded in the lookback window. Run the prohibited
            step in the Demo Cockpit to trip the guardrail, then refresh.
          </p>
        )}
      </Card>
    </div>
  );
}
