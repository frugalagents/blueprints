import type { ReactNode } from "react";
import styles from "./primitives.module.css";

// Chip / StatusChip — the shared pill primitive for statuses, tags, and
// counts. `tone` maps to the semantic palette; `dot` renders a leading status
// dot in the current color (used for health / rail entries).

export type ChipTone =
  | "neutral"
  | "success"
  | "danger"
  | "warning"
  | "info"
  | "gold";

const TONE_CLASS: Record<ChipTone, string> = {
  neutral: styles.chipNeutral,
  success: styles.chipSuccess,
  danger: styles.chipDanger,
  warning: styles.chipWarning,
  info: styles.chipInfo,
  gold: styles.chipGold,
};

export default function Chip({
  children,
  tone = "neutral",
  dot = false,
  title,
}: {
  children: ReactNode;
  tone?: ChipTone;
  dot?: boolean;
  title?: string;
}) {
  return (
    <span className={`${styles.chip} ${TONE_CLASS[tone]}`} title={title}>
      {dot && <span className={styles.dot} aria-hidden="true" />}
      {children}
    </span>
  );
}
