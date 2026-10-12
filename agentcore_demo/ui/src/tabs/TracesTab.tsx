import { useEffect, useState } from "react";
import styles from "./Operate.module.css";
import Chip from "../components/Chip";
import TabHeader from "./TabHeader";
import { useDataClient } from "../data/DataClientProvider";
import type { TraceDetail, TraceSummary } from "../types";

// Traces (CAP-F · Observability): master-detail. Left pane = scrollable list of
// trace ids; right pane = the selected trace's span WATERFALL (time-offset bars
// with a time axis, real span ids / latency / token counts). The two panes
// scroll independently so you never scroll between picking a trace and reading
// its spans.

const KIND_COLOR: Record<string, string> = {
  model: "var(--cap-observability)",
  tool: "var(--cap-gateway)",
  guardrail: "var(--cap-guardrail)",
};

// Number of vertical gridlines / axis ticks on the waterfall.
const TICKS = 4;

export default function TracesTab() {
  const client = useDataClient();
  const [list, setList] = useState<TraceSummary[]>([]);
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    client
      .listTraces(25)
      .then((d) => {
        setList(d.traces);
        // Auto-select the first trace so the waterfall pane is never empty.
        if (d.traces.length) open(d.traces[0].trace_id);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client]);

  async function open(id: string) {
    setSelectedId(id);
    try {
      setDetail(await client.getTrace(id));
    } catch (e) {
      setError(String(e));
    }
  }

  // Derive the waterfall timeline from the spans themselves (min start → max
  // end) rather than the trace's duration_ms, which can be on a different scale
  // than the span offsets. This keeps every bar inside the track.
  const spans = detail?.spans ?? [];
  const t0 = spans.length ? Math.min(...spans.map((s) => s.start_ms)) : 0;
  const t1 = spans.length
    ? Math.max(...spans.map((s) => s.start_ms + s.duration_ms))
    : 1;
  const total = Math.max(t1 - t0, 1);

  return (
    <div className="page">
      <TabHeader
        capability="observability"
        suffix="Observability"
        title="Traces"
        description="ADOT spans for each agent turn in CloudWatch GenAI Observability. Pick a trace on the left; its span waterfall renders on the right with real span ids, latency, and token counts."
      />

      {error && <p className={styles.errorMsg}>{error}</p>}

      <div className={styles.traceSplit}>
        {/* ---------------------------- LEFT: trace list ---------------------- */}
        <aside className={styles.traceList}>
          <div className={styles.traceListHead}>
            Traces{list.length ? ` · ${list.length}` : ""}
          </div>
          <div className={styles.traceListBody}>
            {loading && <p className={styles.emptyMsg}>Loading…</p>}
            {!loading && list.length === 0 && !error && (
              <p className={styles.emptyMsg}>
                No traces in the lookback window yet. Run a turn in the Demo
                Cockpit, then refresh — ADOT spans land asynchronously.
              </p>
            )}
            {list.map((t) => (
              <button
                key={t.trace_id}
                className={`${styles.traceItem} ${
                  selectedId === t.trace_id ? styles.traceItemActive : ""
                }`}
                onClick={() => open(t.trace_id)}
              >
                <div className={styles.traceItemTop}>
                  <span className={styles.traceItemId}>{t.trace_id}</span>
                  <Chip tone={t.status === "OK" ? "success" : "danger"} dot>
                    {t.status}
                  </Chip>
                </div>
                <div className={styles.traceItemMeta}>
                  <span className={styles.mono}>{t.session_id || "—"}</span>
                </div>
                <div className={styles.traceItemMeta}>
                  {t.started_at} · {t.duration_ms} ms · {t.span_count} spans
                </div>
              </button>
            ))}
          </div>
        </aside>

        {/* --------------------------- RIGHT: waterfall ----------------------- */}
        <section className={styles.traceDetail}>
          {!detail ? (
            <p className={styles.emptyMsg}>Select a trace to view its waterfall.</p>
          ) : (
            <>
              <div className={styles.waterHeader}>
                <div>
                  <span className={styles.waterTitle}>Span waterfall</span>{" "}
                  <span className={styles.mono}>{detail.trace_id}</span>
                </div>
                <div className={styles.waterLegend}>
                  {Object.entries(KIND_COLOR).map(([k, c]) => (
                    <span key={k} className={styles.legendItem}>
                      <span
                        className={styles.legendSwatch}
                        style={{ background: c }}
                      />
                      {k}
                    </span>
                  ))}
                </div>
              </div>

              {/* time axis */}
              <div className={styles.waterAxis}>
                <div className={styles.waterAxisName} />
                <div className={styles.waterAxisTrack}>
                  {Array.from({ length: TICKS + 1 }).map((_, i) => (
                    <span
                      key={i}
                      className={styles.waterTick}
                      style={{ left: `${(i / TICKS) * 100}%` }}
                    >
                      {Math.round((total * i) / TICKS)} ms
                    </span>
                  ))}
                </div>
                <div className={styles.waterAxisMeta} />
              </div>

              <div className={styles.waterfall}>
                {detail.spans.map((s) => {
                  const leftPct = ((s.start_ms - t0) / total) * 100;
                  const widthPct = Math.max((s.duration_ms / total) * 100, 0.8);
                  // Label sits outside the bar on whichever side has room.
                  const labelRight = leftPct + widthPct < 80;
                  return (
                    <div key={s.span_id} className={styles.waterRow}>
                      <div
                        className={styles.waterName}
                        title={`${s.name} · span ${s.span_id} · ${s.kind}`}
                      >
                        {s.name}
                      </div>
                      <div className={styles.waterTrack}>
                        {/* gridlines */}
                        {Array.from({ length: TICKS + 1 }).map((_, i) => (
                          <span
                            key={i}
                            className={styles.waterGrid}
                            style={{ left: `${(i / TICKS) * 100}%` }}
                          />
                        ))}
                        <div
                          className={styles.waterBar}
                          style={{
                            left: `${leftPct}%`,
                            width: `${widthPct}%`,
                            background: KIND_COLOR[s.kind] ?? "var(--slate-500)",
                          }}
                          title={`${s.kind} · ${s.duration_ms} ms`}
                        >
                          <span
                            className={`${styles.waterBarLabel} ${
                              labelRight ? styles.waterBarLabelRight : styles.waterBarLabelLeft
                            }`}
                          >
                            {s.duration_ms} ms
                            {s.tokens != null ? ` · ${s.tokens} tok` : ""}
                          </span>
                        </div>
                      </div>
                      <div className={styles.waterMeta}>
                        <span className={styles.mono}>{s.span_id}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
