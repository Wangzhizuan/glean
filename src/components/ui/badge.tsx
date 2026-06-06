import type { ReactNode } from "react";
import { cx } from "@/lib/class-names";

type BadgeTone = "neutral" | "success" | "working" | "warning";

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: BadgeTone;
}) {
  return (
    <span className={cx("badge", tone !== "neutral" && `badge--${tone}`)}>
      <span className="badge__dot" />
      {children}
    </span>
  );
}
