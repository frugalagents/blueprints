import type { CSSProperties, ReactNode } from "react";
import styles from "./primitives.module.css";

// Card — the shared surface primitive: subtle border + soft shadow. `pad`
// adds the standard internal padding; `interactive` adds hover affordance for
// clickable cards (e.g. the Overview capability cards that deep-link).

export default function Card({
  children,
  pad = true,
  interactive = false,
  className,
  style,
  onClick,
  as: As = "div",
}: {
  children: ReactNode;
  pad?: boolean;
  interactive?: boolean;
  className?: string;
  style?: CSSProperties;
  onClick?: () => void;
  as?: "div" | "section" | "article";
}) {
  const cls = [
    styles.card,
    pad ? styles.cardPad : "",
    interactive ? styles.cardInteractive : "",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <As className={cls} style={style} onClick={onClick}>
      {children}
    </As>
  );
}
