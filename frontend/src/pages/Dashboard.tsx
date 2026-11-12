/** Dashboard (issue #123): the user-arrangeable landing surface.
 * Widget order/hidden persist per member via /profiles/me/dashboard;
 * standalone mode (no profile) falls back to localStorage. Widgets
 * come from the registry — unavailable ones degrade cleanly. */
import { Component, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowDown, ArrowRight, ArrowUp, Eye, EyeOff, Inbox, Plus, SlidersHorizontal } from "lucide-react";
import { api } from "../api/client";
import { useApi, useHouseholds } from "../api/useApi";
import { useReview } from "../api/useReview";
import type { components } from "../api/schema";
import { Button } from "../components/Button";
import { Sheet } from "../components/Dialog";
import { PageHeader } from "../components/Display";
import { ErrorState, Skeleton } from "../components/Feedback";
import { cn } from "../lib/cn";
import { formatLongDate } from "../lib/format";
import { useShell } from "../shell/ShellContext";
import { DEFAULT_ORDER, WIDGETS, widgetById } from "../widgets/registry";

type Role = components["schemas"]["Role"];
interface Layout { order: string[]; hidden: string[] }

const LS_KEY = "ff.dashboard";
const SPAN: Record<1 | 2 | 3, string> = {
  1: "lg:col-span-1",
  2: "md:col-span-2 lg:col-span-2",
  3: "md:col-span-2 lg:col-span-3",
};

function loadLocal(): Layout {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) return JSON.parse(raw) as Layout;
  } catch { /* fall through */ }
  return { order: DEFAULT_ORDER, hidden: [] };
}

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

function normalise(l: Layout): Layout {
  const ids = new Set(WIDGETS.map((w) => w.id));
  const order = [...new Set(
    [...l.order, ...DEFAULT_ORDER].filter((id) => ids.has(id)))];
  return { order, hidden: l.hidden.filter((id) => ids.has(id)) };
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
  const ready = households.data !== null;
  const { openQuickAdd } = useShell();
  const remote = useApi(
    () => api.get("/api/v1/profiles/me/dashboard"), ready);
  const [layout, setLayout] = useState<Layout | null>(null);
  const [customising, setCustomising] = useState(false);
  const [role, setRole] = useState<Role | null>(null);

  const hydratedRemote = useRef(false);

  // Remote wins when a profile exists; standalone falls back to
  // localStorage (issue #123: per-member persistence, clean degrade).
  // Local layout is temporary during startup failure; the first recovered
  // profile response restores both member layout and save permissions.
  useEffect(() => {
    if (remote.data !== null && !hydratedRemote.current) {
      hydratedRemote.current = true;
      setRole(remote.data.role ?? null);
      const saved = remote.data.layout;
      setLayout(normalise(saved
        ? { order: saved.order ?? [], hidden: saved.hidden ?? [] }
        : loadLocal()));
    } else if (layout === null && (households.error || remote.error)) {
      setLayout(normalise(loadLocal()));
    }
  }, [remote.data, remote.error, layout, households.error]);

  const save = (next: Layout) => {
    setLayout(next);
    localStorage.setItem(LS_KEY, JSON.stringify(next));
    if (role !== null) {
      void api.put("/api/v1/profiles/me/dashboard",
        { body: next }).catch(() => { /* remote optional */ });
    }
  };

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
    !w.roles || role === null || w.roles.includes(role);

  const visible = layout?.order
    .map(widgetById)
    .filter((w): w is NonNullable<typeof w> => w !== undefined)
    .filter((w) => !layout.hidden.includes(w.id))
    .filter(allowed)
    ?? [];

  const now = new Date();
  return (
    <div>
      <PageHeader
        eyebrow={formatLongDate(now)}
        title={greeting(now)}
        description={households.data?.[0] ? `Here's where ${households.data[0].name} stands today.` : undefined}
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
            Reorder or hide widgets. Your layout is saved to your profile.
          </p>
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
                    <Button variant="ghost" size="icon-sm" aria-label={`Move ${w.title} up`} disabled={idx === 0} onClick={() => move(id, -1)}>
                      <ArrowUp />
                    </Button>
                    <Button variant="ghost" size="icon-sm" aria-label={`Move ${w.title} down`} disabled={idx === layout.order.length - 1} onClick={() => move(id, 1)}>
                      <ArrowDown />
                    </Button>
                    <Button variant="ghost" size="icon-sm" aria-label={`${hidden ? "Show" : "Hide"} ${w.title}`} onClick={() => toggleHide(id)}>
                      {hidden ? <EyeOff /> : <Eye />}
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
          <Button variant="ghost" className="mt-4" onClick={() => save({ order: DEFAULT_ORDER, hidden: [] })}>
            Reset to default
          </Button>
        </div>
      </Sheet>
    </div>
  );
}
