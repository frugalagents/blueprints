import styles from "./primitives.module.css";
import { CAPABILITIES, type CapabilityKey } from "../capabilities";

// CapabilityBadge — a small pill naming an AgentCore capability (letter A–G in
// its accent color + label). Used in Operate-tab headers and the capability map
// so a viewer always knows which of the 7 capabilities a view demonstrates.

export default function CapabilityBadge({
  capability,
  suffix,
}: {
  capability: CapabilityKey;
  /** Optional trailing text, e.g. "Observability" after the label. */
  suffix?: string;
}) {
  const cap = CAPABILITIES[capability];
  return (
    <span className={styles.capBadge}>
      <span
        className={styles.capLetter}
        style={{ background: cap.colorVar }}
        aria-hidden="true"
      >
        {cap.letter}
      </span>
      {suffix ? `${cap.label} · ${suffix}` : cap.label}
    </span>
  );
}
