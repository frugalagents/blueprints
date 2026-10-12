import type { ButtonHTMLAttributes, ReactNode } from "react";
import styles from "./primitives.module.css";

// Button — the shared action primitive. `primary` is the muted-gold action,
// `secondary` is a navy outline, `ghost` is a subtle text button.

type Variant = "primary" | "secondary" | "ghost";

export default function Button({
  children,
  variant = "primary",
  small = false,
  className,
  ...rest
}: {
  children: ReactNode;
  variant?: Variant;
  small?: boolean;
  className?: string;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  const variantCls =
    variant === "primary"
      ? styles.btnPrimary
      : variant === "secondary"
      ? styles.btnSecondary
      : styles.btnGhost;
  const cls = [styles.btn, variantCls, small ? styles.btnSm : "", className ?? ""]
    .filter(Boolean)
    .join(" ");
  return (
    <button className={cls} {...rest}>
      {children}
    </button>
  );
}
