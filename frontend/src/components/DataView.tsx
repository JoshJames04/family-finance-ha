import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import type { ApiState } from "../api/useApi";
import { EmptyState, ErrorState, Skeleton } from "./Feedback";

/** Standard loading / error / empty / data boundary for list pages. */
export function DataView<T>({
  state,
  empty,
  emptyDescription,
  emptyIcon,
  emptyAction,
  loading,
  children,
}: {
  state: ApiState<T>;
  empty: string;
  emptyDescription?: ReactNode;
  emptyIcon?: LucideIcon;
  emptyAction?: ReactNode;
  /** Custom skeleton; defaults to three shimmer rows. */
  loading?: ReactNode;
  children: (data: NonNullable<T>) => ReactNode;
}) {
  if (state.loading)
    return (
      <div role="status" aria-live="polite" aria-label="Loading">
        <span className="sr-only">Loading…</span>
        {loading ?? (
          <div className="space-y-2.5">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-4 w-3/5" />
          </div>
        )}
      </div>
    );
  // Terminal failure with nothing usable to show → honest error.
  if (state.error && state.data == null)
    return <ErrorState message={state.error} onRetry={state.refetch} />;
  if (
    state.data == null ||
    (Array.isArray(state.data) && state.data.length === 0)
  )
    return empty ? (
      <EmptyState
        compact
        title={empty}
        description={emptyDescription}
        icon={emptyIcon}
        action={emptyAction}
      />
    ) : null;
  // Stale data alongside a terminal refresh error keeps rendering —
  // last known-good records beat an empty panel (#460) — with a
  // slim warning so it can't be mistaken for live data.
  return (
    <>
      {state.error ? (
        <ErrorState message={state.error} onRetry={state.refetch} />
      ) : null}
      {children(state.data as NonNullable<T>)}
    </>
  );
}
