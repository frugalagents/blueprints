import type { ReactNode } from "react";
import styles from "./primitives.module.css";

// SectionHeader — a page / section title with an optional subtitle and a
// right-aligned actions slot. Used at the top of every Operate tab (with a
// CapabilityBadge in `badge`) and on the Overview / Cockpit screens.

export default function SectionHeader({
  title,
  subtitle,
  badge,
  actions,
}: {
  title: string;
  subtitle?: ReactNode;
  badge?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className={styles.sectionHeader}>
      <div className={styles.sectionTitleRow}>
        {badge}
        <h1 className={styles.sectionTitle}>{title}</h1>
        {actions && <span style={{ marginLeft: "auto" }}>{actions}</span>}
      </div>
      {subtitle && <p className={styles.sectionSubtitle}>{subtitle}</p>}
    </header>
  );
}
