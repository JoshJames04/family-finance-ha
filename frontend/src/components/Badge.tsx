import type { ReactNode } from "react";
import { cn } from "../lib/cn";

export type BadgeTone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "error"
  | "accent";

const TONE: Record<BadgeTone, string> = {
  neutral: "bg-surface-2 text-fg-muted border-border",
  info: "bg-info-soft text-info border-info/20",
  success: "bg-positive-soft text-positive border-positive/20",
  warning: "bg-warning-soft text-warning border-warning/25",
  error: "bg-negative-soft text-negative border-negative/20",
  accent: "bg-accent-soft text-accent border-accent/25",
};

/** Status badge — the single way to render state labels. A leading
 * dot carries the tone so state never relies on colour alone. */
export function Badge({
  tone = "neutral",
  dot = true,
  className,
  children,
}: {
  tone?: BadgeTone;
  dot?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        `ff-badge ff-badge--${tone}`,
        "inline-flex h-5.5 items-center gap-1.5 rounded-full border px-2 text-[11.5px] font-medium capitalize whitespace-nowrap",
        TONE[tone],
        className,
      )}
    >
      {dot && <span aria-hidden className="size-1.5 rounded-full bg-current opacity-80" />}
      {children}
    </span>
  );
}
