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
  if (state.error) return <ErrorState message={state.error} />;
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
  return <>{children(state.data as NonNullable<T>)}</>;
}
