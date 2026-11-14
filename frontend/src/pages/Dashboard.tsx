/** Dashboard (issue #123): the user-arrangeable landing surface.
 * Widget order/hidden persist per member via /profiles/me/dashboard;
 * standalone mode (no profile) falls back to localStorage. Widgets
 * come from the registry — unavailable ones degrade cleanly. */
import { Component, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowDown, ArrowRight, ArrowUp, Eye, EyeOff, Inbox, Plus, SlidersHorizontal } from "lucide-react";
import { getHouseholdId } from "../api/client";
import { useHouseholds } from "../api/useApi";
import { useDashboardPreferences } from "../api/useDashboardPreferences";
import { useReview } from "../api/useReview";
import type { components } from "../api/schema";
import { Button } from "../components/Button";
import { Sheet } from "../components/Dialog";
import { PageHeader } from "../components/Display";
import { ErrorState, Skeleton } from "../components/Feedback";
import { cn } from "../lib/cn";
import { formatLongDate } from "../lib/format";
import { useShell } from "../shell/ShellContext";
import { DEFAULT_ORDER, widgetById } from "../widgets/registry";

type Role = components["schemas"]["Role"];
const SPAN: Record<1 | 2 | 3, string> = {
  1: "lg:col-span-1",
  2: "md:col-span-2 lg:col-span-2",
  3: "md:col-span-2 lg:col-span-3",
};

/** Per-widget error boundary — one failing widget degrades to a muted
 * card instead of taking the whole dashboard down (issue #123). */
class WidgetBoundary extends Component<
  { title: string; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed
      ? (
        <div className="grid h-full min-h-24 place-items-center rounded-[var(--radius-card)] border border-dashed border-border p-4 text-[13px] text-fg-subtle">
          {this.props.title} unavailable.
        </div>
      )
      : this.props.children;
  }
}

function greeting(d: Date): string {
  const h = d.getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

function ReviewCallout() {
  const review = useReview();
  if (review.total === 0) return null;
  return (
    <Link
      to="/review"
      className="mb-4 flex items-center gap-3 rounded-xl border border-warning/30 bg-warning-soft px-4 py-3 text-[13.5px] text-fg transition-colors hover:border-warning/50"
    >
      <span className="grid size-8 place-items-center rounded-lg bg-warning/15 text-warning"><Inbox className="size-4" /></span>
      <span className="flex-1">
        <b className="font-semibold">{review.total} item{review.total === 1 ? "" : "s"} need{review.total === 1 ? "s" : ""} your review</b>
        <span className="text-fg-muted"> — matches, detections and unusual activity</span>
      </span>
      <ArrowRight className="size-4 text-fg-muted" />
    </Link>
  );
}

export function Dashboard() {
  const households = useHouseholds();
  const household = households.data?.find((h) => h.id === getHouseholdId()) ?? households.data?.[0];
  const ready = households.data !== null;
  const { openQuickAdd } = useShell();
  const preferences = useDashboardPreferences(ready, households.error);
  const { layout, role, save } = preferences;
  const [customising, setCustomising] = useState(false);

  const move = (id: string, dir: -1 | 1) => {
    if (!layout) return;
    const order = [...layout.order];
    const i = order.indexOf(id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j]!, order[i]!];
    save({ ...layout, order });
  };
  const toggleHide = (id: string) => {
    if (!layout) return;
    const hidden = layout.hidden.includes(id)
      ? layout.hidden.filter((h) => h !== id)
      : [...layout.hidden, id];
    save({ ...layout, hidden });
  };

  const allowed = (w: { roles?: Role[] }) =>
    // Standalone (no resolved profile) is a single operator — see
    // everything; members get role-gated widgets.
    !w.roles || role === null || (role !== undefined && w.roles.includes(role));

  const visible = layout?.order
    .map(widgetById)
    .filter((w): w is NonNullable<typeof w> => w !== undefined)
    .filter((w) => !layout.hidden.includes(w.id))
    .filter(allowed)
    ?? [];

  const status = (
    <div className="mb-4 text-[13px] text-fg-muted" aria-live="polite">
      {preferences.saving ? <p>Saving layout…</p> : preferences.dirty ? (
        <div role="alert" className="flex flex-wrap items-center gap-2">
          <p className="w-full wrap-anywhere">Changes not saved. {preferences.error ?? "Your draft is kept in this browser tab."}</p>
          <Button variant="secondary" size="sm" className="min-h-11" disabled={!preferences.canRetry} onClick={preferences.retry}>
            Retry saving layout
          </Button>
          <Button variant="ghost" size="sm" className="min-h-11" disabled={!preferences.canRevert} onClick={preferences.revert}>
            Revert layout
          </Button>
        </div>
      ) : preferences.saved ? <p>{role === null ? "Layout saved in this browser." : "Layout saved to your profile."}</p> : null}
    </div>
  );
  const now = new Date();
  return (
    <div>
      <PageHeader
        eyebrow={formatLongDate(now)}
        title={greeting(now)}
        description={household ? `Here's where ${household.name} stands today.` : undefined}
        actions={
          <>
            <Button variant="secondary" onClick={() => setCustomising(true)}>
              <SlidersHorizontal /> Customise
            </Button>
            <Button variant="primary" onClick={openQuickAdd} className="hidden md:inline-flex">
              <Plus /> Add transaction
            </Button>
          </>
        }
      />
      {households.error && !households.loading && (
        <ErrorState message={households.error} onRetry={households.refetch} />
      )}
      {preferences.remoteError && (
        <ErrorState message={preferences.remoteError} onRetry={preferences.refetch} />
      )}
      {!customising && status}
      <ReviewCallout />
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {layout === null && (
          <>
            <Skeleton className="h-40 rounded-[var(--radius-card)]" />
            <Skeleton className="h-40 rounded-[var(--radius-card)]" />
            <Skeleton className="h-40 rounded-[var(--radius-card)]" />
          </>
        )}
        {visible.map((w) => (
          <div key={w.id} data-widget={w.id} className={cn("min-w-0", SPAN[w.span])}>
            <WidgetBoundary title={w.title}>
              <w.Component title={w.title} />
            </WidgetBoundary>
          </div>
        ))}
      </div>

      <Sheet open={customising} onOpenChange={setCustomising} title="Customise dashboard" side="right">
        <div className="p-4">
          <p className="mb-4 text-[13px] text-fg-muted">
            {preferences.canRetry ? "Reorder or hide widgets." : "Loading your saved layout before editing."}
          </p>
          {customising && status}
          {layout && (
            <ul aria-label="Customise widgets" className="flex flex-col gap-1.5">
              {layout.order.map((id, idx) => {
                const w = widgetById(id);
                if (!w) return null;
                const hidden = layout.hidden.includes(id);
                const roleBlocked = !allowed(w);
                return (
                  <li key={id} className={cn("flex items-center gap-2 rounded-xl border border-border bg-surface-2 px-3 py-2", hidden && "opacity-60")}>
                    <span className="flex-1 truncate text-[13.5px] font-medium">{w.title}</span>
                    {roleBlocked && <span className="text-[11.5px] text-fg-subtle">admin only</span>}
                    <Button variant="ghost" size="icon-sm" aria-label={`Move ${w.title} up`} disabled={preferences.saving || !preferences.canRetry || idx === 0} onClick={() => move(id, -1)}>
                      <ArrowUp />
                    </Button>
                    <Button variant="ghost" size="icon-sm" aria-label={`Move ${w.title} down`} disabled={preferences.saving || !preferences.canRetry || idx === layout.order.length - 1} onClick={() => move(id, 1)}>
                      <ArrowDown />
                    </Button>
                    <Button variant="ghost" size="icon-sm" disabled={preferences.saving || !preferences.canRetry} aria-label={`${hidden ? "Show" : "Hide"} ${w.title}`} onClick={() => toggleHide(id)}>
                      {hidden ? <EyeOff /> : <Eye />}
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
          <Button variant="ghost" className="mt-4" disabled={preferences.saving || !preferences.canRetry} onClick={() => save({ order: DEFAULT_ORDER, hidden: [] })}>
            Reset to default
          </Button>
        </div>
      </Sheet>
    </div>
  );
}
