import { useEffect, useState } from "react";
import styles from "./Operate.module.css";
import Button from "../components/Button";
import Chip from "../components/Chip";
import TabHeader from "./TabHeader";
import { useDataClient } from "../data/DataClientProvider";
import type { SessionDetail, SessionSummary } from "../types";

// Memory (CAP-C · AgentCore Memory): SHORT-TERM conversation memory — the
// per-session turn history that lets an advisor keep talking about one client
// across turns without restating context. Master-detail: sessions on the left,
// the selected session's turns on the right; each pane scrolls independently.

export default function MemoryTab() {
  const client = useDataClient();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  function load() {
    setLoading(true);
    client
      .listSessions()
      .then((d) => {
        setSessions(d.sessions);
        if (d.sessions.length && !selectedId) open(d.sessions[0].id);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }

  useEffect(load, [client]); // eslint-disable-line react-hooks/exhaustive-deps

  async function open(id: string) {
    setSelectedId(id);
    try {
      setDetail(await client.getSession(id));
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="page">
      <TabHeader
        capability="memory"
        suffix="Memory"
        title="Memory"
        description="Short-term conversation memory: per-session turn history that lets the advisor keep talking about one client across turns without restating context."
        actions={
          <Button variant="secondary" small onClick={load}>
            Refresh
          </Button>
        }
      />

      <div className={styles.memNote}>
        <strong>Short-term (session) memory.</strong> Each session keeps an
        ordered list of user/assistant turns, keyed by{" "}
        <span className={styles.mono}>sessionId</span>. In production this is
        backed by the deployed <strong>AgentCore Memory</strong> resource
        (30-day event expiry); the demo writes to the same interface from the
        Cockpit chat. No long-term / semantic memory strategy is configured for
        this POC.
      </div>

      {error && <p className={styles.errorMsg}>{error}</p>}

      <div className={styles.traceSplit}>
        {/* ----------------------------- LEFT: sessions --------------------- */}
        <aside className={styles.traceList}>
          <div className={styles.traceListHead}>
            Sessions{sessions.length ? ` · ${sessions.length}` : ""}
          </div>
          <div className={styles.traceListBody}>
            {loading && <p className={styles.emptyMsg}>Loading…</p>}
            {!loading && sessions.length === 0 && !error && (
              <p className={styles.emptyMsg}>
                No active sessions yet. Run a turn in the Demo Cockpit.
              </p>
            )}
            {sessions.map((s) => (
              <button
                key={s.id}
                className={`${styles.traceItem} ${
                  selectedId === s.id ? styles.traceItemActive : ""
                }`}
                onClick={() => open(s.id)}
              >
                <div className={styles.traceItemTop}>
                  <span className={styles.traceItemId}>{s.id}</span>
                  <Chip tone="neutral">{s.turn_count} turns</Chip>
                </div>
              </button>
            ))}
          </div>
        </aside>

        {/* --------------------------- RIGHT: turns ------------------------- */}
        <section className={styles.traceDetail}>
          {!detail ? (
            <p className={styles.emptyMsg}>Select a session to read its turns.</p>
          ) : (
            <>
              <div className={styles.waterHeader}>
                <div>
                  <span className={styles.waterTitle}>Conversation turns</span>{" "}
                  <span className={styles.mono}>{detail.id}</span>
                </div>
                <Chip tone="neutral">{detail.events.length} turns</Chip>
              </div>

              {detail.events.length === 0 ? (
                <p className={styles.emptyMsg}>No turns recorded yet.</p>
              ) : (
                <div className={styles.turns}>
                  {detail.events.map((ev, i) => {
                    const isUser = ev.role === "user";
                    return (
                      <div
                        key={i}
                        className={`${styles.turn} ${
                          isUser ? styles.turnUser : styles.turnAssistant
                        }`}
                      >
                        <div className={styles.turnRole}>
                          {isUser ? "Advisor" : "Copilot"}
                        </div>
                        <div className={styles.turnBubble}>{ev.content}</div>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
