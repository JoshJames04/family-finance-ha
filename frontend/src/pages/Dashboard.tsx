/** Dashboard (issue #123): the user-arrangeable landing surface.
 * Widget order/hidden persist per member via /profiles/me/dashboard;
 * standalone mode (no profile) falls back to localStorage. Widgets
 * come from the registry — unavailable ones degrade cleanly. */
import { Component, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api/client";
import { useApi, useHouseholds } from "../api/useApi";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import {
  DEFAULT_ORDER,
  WIDGETS,
  widgetById,
} from "../widgets/registry";

type Role = components["schemas"]["Role"];
interface Layout { order: string[]; hidden: string[] }

const LS_KEY = "ff.dashboard";

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
      ? <p className="ff-muted">{this.props.title} unavailable.</p>
      : this.props.children;
  }
}

function normalise(l: Layout): Layout {
  const ids = new Set(WIDGETS.map((w) => w.id));
  const order = [...new Set(
    [...l.order, ...DEFAULT_ORDER].filter((id) => ids.has(id)))];
  return { order, hidden: l.hidden.filter((id) => ids.has(id)) };
}

export function Dashboard() {
  const households = useHouseholds();
  const ready = households.data !== null;
  const remote = useApi(
    () => api.get("/api/v1/profiles/me/dashboard"), ready);
  const [layout, setLayout] = useState<Layout | null>(null);
  const [customising, setCustomising] = useState(false);
  const [role, setRole] = useState<Role | null>(null);

  // Remote wins when a profile exists; standalone falls back to
  // localStorage (issue #123: per-member persistence, clean degrade).
  useEffect(() => {
    if (layout !== null || remote.loading || !ready) return;
    if (remote.data === null && remote.error === null) return;
    setRole(remote.data?.role ?? null);
    const remoteLayout = remote.data?.layout;
    setLayout(normalise(remoteLayout
      ? { order: remoteLayout.order ?? [], hidden: remoteLayout.hidden ?? [] }
      : loadLocal()));
  }, [ready, remote.loading, remote.data, remote.error, layout]);

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

  const visible = layout?.order
    .map(widgetById)
    .filter((w): w is NonNullable<typeof w> => w !== undefined)
    .filter((w) => !layout.hidden.includes(w.id))
    // Standalone (no resolved profile) is a single operator — see
    // everything; members get role-gated widgets.
    .filter((w) => !w.roles || role === null || w.roles.includes(role))
    ?? [];

  return (
    <section>
      <h1>Dashboard</h1>
      <DataView state={households} empty="No household configured yet.">
        {(hs) => <p className="ff-muted">Household: {hs[0]!.name}</p>}
      </DataView>
      <p>
        <button type="button"
          onClick={() => setCustomising((c) => !c)}>
          {customising ? "Done" : "Customise"}
        </button>
      </p>
      {customising && layout && (
        <ul className="ff-list" aria-label="Customise widgets">
          {layout.order.map((id) => {
            const w = widgetById(id);
            if (!w) return null;
            const roleBlocked = w.roles != null
              && role !== null && !w.roles.includes(role);
            return (
              <li key={id}>
                <span>{w.title}</span>
                {roleBlocked && (
                  <span className="ff-muted"> admin only</span>
                )}
                <button aria-label={`Move ${w.title} up`}
                  onClick={() => move(id, -1)}>↑</button>
                <button aria-label={`Move ${w.title} down`}
                  onClick={() => move(id, 1)}>↓</button>
                <button aria-label={`${layout.hidden.includes(id) ? "Show" : "Hide"} ${w.title}`}
                  onClick={() => toggleHide(id)}>
                  {layout.hidden.includes(id) ? "show" : "hide"}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <div className="ff-card-grid">
        {visible.map((w) => (
          <div className="ff-card" key={w.id} data-widget={w.id}>
            <h2>{w.title}</h2>
            <WidgetBoundary title={w.title}>
              <w.Component />
            </WidgetBoundary>
          </div>
        ))}
      </div>
    </section>
  );
}
