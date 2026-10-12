import { useEffect, useState } from "react";
import styles from "./Operate.module.css";
import Card from "../components/Card";
import Chip, { type ChipTone } from "../components/Chip";
import Button from "../components/Button";
import TabHeader from "./TabHeader";
import { useDataClient } from "../data/DataClientProvider";
import type { RegistryRecord } from "../types";

// Registry (CAP-E · Registry / governance): live catalog records (agent + MCP
// tools) with governance state, and a working Approve action that walks a
// record DRAFT → PENDING_APPROVAL → APPROVED against the real registry.

const STATE_TONE: Record<string, ChipTone> = {
  DRAFT: "neutral",
  PENDING_APPROVAL: "warning",
  APPROVED: "success",
  REJECTED: "danger",
};

export default function RegistryTab() {
  const client = useDataClient();
  const [records, setRecords] = useState<RegistryRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  function load() {
    client
      .getRegistry()
      .then((d) => setRecords(d.records))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }

  useEffect(load, [client]); // eslint-disable-line react-hooks/exhaustive-deps

  async function approve(id: string) {
    setBusy(id);
    setError(null);
    try {
      const updated = await client.approveRegistry(id);
      setRecords((prev) => prev.map((r) => (r.id === id ? updated : r)));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="page">
      <TabHeader
        capability="registry"
        suffix="Registry / Governance"
        title="Registry"
        description="The agent and its MCP tools catalogued for discovery and governance. Approve a PENDING_APPROVAL record to walk the DRAFT → PENDING_APPROVAL → APPROVED compliance workflow against the live registry."
      />

      {error && <p className={styles.errorMsg}>{error}</p>}

      <Card pad={false} style={{ overflow: "hidden" }}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Record id</th>
              <th>Name</th>
              <th>Type</th>
              <th>Governance state</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {records.map((r) => (
              <tr key={r.id}>
                <td className={styles.mono}>{r.id}</td>
                <td>{r.name}</td>
                <td>
                  <Chip tone={r.type === "agent" ? "info" : "neutral"}>
                    {r.type === "agent" ? "AGENT" : "MCP"}
                  </Chip>
                </td>
                <td>
                  <Chip tone={STATE_TONE[r.governance_state] ?? "neutral"} dot>
                    {r.governance_state}
                  </Chip>
                </td>
                <td>
                  {(r.governance_state === "PENDING_APPROVAL" ||
                    r.governance_state === "DRAFT") && (
                    <Button
                      small
                      disabled={busy === r.id}
                      onClick={() => approve(r.id)}
                    >
                      {busy === r.id ? "Approving…" : "Approve"}
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && records.length === 0 && !error && (
          <p className={styles.emptyMsg} style={{ padding: "0 12px 12px" }}>
            No registry records returned.
          </p>
        )}
      </Card>
    </div>
  );
}
