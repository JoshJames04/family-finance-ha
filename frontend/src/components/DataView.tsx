import type { ReactNode } from "react";
import type { ApiState } from "../api/useApi";

/** Standard loading / error / empty / data boundary for list pages. */
export function DataView<T>({
  state,
  empty,
  children,
}: {
  state: ApiState<T>;
  empty: string;
  children: (data: NonNullable<T>) => ReactNode;
}) {
  if (state.loading) return <p className="ff-muted">Loading…</p>;
  if (state.error)
    return (
      <p className="ff-error" role="alert">
        {state.error}
      </p>
    );
  if (
    state.data == null ||
    (Array.isArray(state.data) && state.data.length === 0)
  )
    return <p className="ff-muted">{empty}</p>;
  return <>{children(state.data as NonNullable<T>)}</>;
}
