import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { AlertTriangle, Inbox } from "lucide-react";
import { cn } from "../lib/cn";

/** Shimmer placeholder sized by className. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn("animate-pulse rounded-md bg-surface-3", className)}
    />
  );
}

/** Empty state: icon, headline, guidance and an optional next step. */
export function EmptyState({
  icon: Icon = Inbox,
  title,
  description,
  action,
  compact = false,
  className,
}: {
  icon?: LucideIcon;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center",
        compact ? "gap-1.5 py-6" : "gap-2.5 py-12",
        className,
      )}
    >
      <div
        className={cn(
          "grid place-items-center rounded-xl border border-border bg-surface-2 text-fg-subtle",
          compact ? "size-9" : "size-11",
        )}
      >
        <Icon className={compact ? "size-4" : "size-5"} />
      </div>
      <p className="ff-muted font-medium text-fg">{title}</p>
      {description && (
        <p className="max-w-sm text-[13px] text-fg-subtle">{description}</p>
      )}
      {action && <div className="mt-1.5">{action}</div>}
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="ff-error flex items-start gap-3 rounded-xl border border-negative/25 bg-negative-soft p-3.5 text-[13px] text-negative"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <div className="flex-1">
        <p className="font-medium">Couldn't load this</p>
        <p className="opacity-90">{message}</p>
      </div>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="font-medium underline underline-offset-4"
        >
          Retry
        </button>
      )}
    </div>
  );
}
