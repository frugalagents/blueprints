import { useEffect, useState } from "react";
import opStyles from "./Operate.module.css";
import styles from "./Policy.module.css";
import Card from "../components/Card";
import Chip, { type ChipTone } from "../components/Chip";
import TabHeader from "./TabHeader";
import { useDataClient } from "../data/DataClientProvider";
import type { PolicyResponse } from "../types";

// Policy (CAP-H · Authorization): the REAL AgentCore Policy Engine + Cedar
// ownership rule, and the live ALLOWED/DENIED decision log from recent turns.
// DISTINCT from Guardrails (CAP-A): Policy is deterministic Cedar authorization
// (what the agent may ACCESS); Guardrails is probabilistic content filtering
// (what the agent may SAY). The two stay separate tabs on purpose.

function statusTone(status: string): ChipTone {
  const s = status.toLowerCase();
  if (s === "active") return "success";
  if (s.startsWith("unknown") || s.includes("fail")) return "danger";
  if (s === "local/none") return "neutral";
  if (s === "creating" || s === "updating") return "warning";
  return "neutral";
}

export default function PolicyTab() {
  const client = useDataClient();
  const [data, setData] = useState<PolicyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    client
      .getPolicy()
      .then(setData)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [client]);

  return (
    <div className="page">
      <TabHeader
        capability="policy"
        suffix="Authorization"
        title="Policy"
        description="Real AgentCore Policy — a deterministic Cedar authorization layer attached to the Gateway in ENFORCE mode. An advisor's agent may read only that advisor's own clients; a non-owned client's portfolio tool call is denied at the Gateway and its data is never returned. This is distinct from Guardrails (content filtering)."
      />

      {error && <p className={opStyles.errorMsg}>{error}</p>}

      {data && (
        <>
          <div className={opStyles.metaLine}>
            <span>Policy engine</span>
            <Chip tone={statusTone(data.engine_status)} dot>
              {data.engine_status}
            </Chip>
            <span>·</span>
            <span>Ownership policy</span>
            <Chip tone={statusTone(data.policy_status)} dot>
              {data.policy_status}
            </Chip>
            <span>·</span>
            <span>Gateway enforcement</span>
            <Chip tone={data.enforcement_mode === "ENFORCE" ? "success" : "neutral"}>
              {data.enforcement_mode}
            </Chip>
            <span>·</span>
            <span>Policy mode</span>
            <Chip tone={data.policy_enforcement === "ACTIVE" ? "success" : "neutral"}>
              {data.policy_enforcement}
            </Chip>
          </div>

          <Card>
            <h3 className={opStyles.subhead} style={{ marginTop: 0 }}>
              Ownership rule
            </h3>
            <p className={styles.modelNote}>
              Cedar denies the portfolio tool unless the requested{" "}
              <code>client_id</code> is in the acting advisor's owned set. The
              owned set is derived from the firm's client book, not hand-typed.
            </p>
            <div className={styles.ruleGrid}>
              <div className={styles.ruleItem}>
                <div className={styles.ruleLabel}>Acting advisor (principal)</div>
                <div className={styles.ruleValue}>
                  <span className="mono">{data.rule.acting_advisor}</span>
                </div>
              </div>
              <div className={styles.ruleItem}>
                <div className={styles.ruleLabel}>Gated tool (action)</div>
                <div className={styles.ruleValue}>
                  <span className="mono">{data.rule.tool || "getClientPortfolio"}</span>
                </div>
              </div>
              <div className={styles.ruleItem}>
                <div className={styles.ruleLabel}>Enforcement mode</div>
                <div className={styles.ruleValue}>{data.rule.enforcement}</div>
              </div>
              <div className={styles.ruleItem}>
                <div className={styles.ruleLabel}>
                  Owned clients (resource allow-set)
                </div>
                <div className={styles.ownedList}>
                  {data.rule.owned_clients.length ? (
                    data.rule.owned_clients.map((c) => (
                      <Chip key={c} tone="info">
                        {c}
                      </Chip>
                    ))
                  ) : (
                    <Chip tone="neutral">none configured</Chip>
                  )}
                </div>
              </div>
            </div>
            <p className={styles.prodNote}>
              Principal / resource model: this Gateway uses AWS_IAM (SigV4)
              inbound auth, so the Cedar principal is an{" "}
              <code>AgentCore::IamEntity</code> and the rule keys on the tool
              input parameter <code>context.input.client_id</code> against the
              acting advisor's owned-client allow-set. In production with a
              CUSTOM_JWT authorizer the same rule keys on an OAuth{" "}
              <code>advisor_id</code> claim instead of a configured id set.
            </p>
          </Card>

          <h3 className={opStyles.subhead}>Decision log (recent turns)</h3>
          <p className={styles.modelNote}>
            Genuine ALLOWED / DENIED decisions from the enforced Gateway, captured
            from the live chat stream this session. Resets on BFF restart (a POC
            in-process log, not a durable audit store).
          </p>
          <Card pad={false} style={{ overflow: "hidden" }}>
            <table className={opStyles.table}>
              <thead>
                <tr>
                  <th>Timestamp</th>
                  <th>Decision</th>
                  <th>Principal</th>
                  <th>Resource (client)</th>
                </tr>
              </thead>
              <tbody>
                {data.decisions.map((d, i) => (
                  <tr key={i}>
                    <td className={opStyles.mono}>{d.timestamp}</td>
                    <td>
                      <Chip tone={d.decision === "ALLOWED" ? "success" : "danger"} dot>
                        {d.decision}
                      </Chip>
                    </td>
                    <td className={opStyles.mono}>{d.principal}</td>
                    <td className={opStyles.mono}>{d.resource || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!loading && data.decisions.length === 0 && !error && (
              <p className={opStyles.emptyMsg} style={{ padding: "0 12px 12px" }}>
                No decisions recorded yet this session. Run the "Access control"
                step in the Demo Cockpit (a non-owned client) to produce a DENIED
                decision, or a normal client step for an ALLOWED one, then refresh.
              </p>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
