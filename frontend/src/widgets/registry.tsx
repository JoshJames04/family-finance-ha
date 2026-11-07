/** Dashboard widget registry (issue #123): each entry is a component
 * reading the read-model APIs — no bespoke data paths. New widgets
 * are added here only; the dashboard framework (order/hidden/layout
 * persistence) needs no change. */
import type { ReactElement } from "react";
import { api } from "../api/client";
import { useApi, useHouseholds } from "../api/useApi";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Money } from "../components/Money";
import { formatDate } from "../lib/format";
import { ProjectionChart } from "../components/ProjectionChart";

type Role = components["schemas"]["Role"];
type Account = components["schemas"]["AccountOut"];
type Movement = components["schemas"]["ExpectedMovementOut"];
type Subscription = components["schemas"]["SubscriptionOut"];
type Projection = components["schemas"]["ProjectionOut"];

export interface WidgetDef {
  id: string;
  title: string;
  /** Roles the widget is available to — omit for everyone. */
  roles?: Role[];
  Component: () => ReactElement;
}

function window90() {
  const start = new Date();
  const end = new Date();
  end.setDate(end.getDate() + 90);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return { start: iso(start), end: iso(end) };
}

function AccountsWidget() {
  const ready = useHouseholds().data !== null;
  const state = useApi(() => api.get("/api/v1/accounts"), ready);
  return (
    <DataView state={state} empty="No accounts yet.">
      {(page) => (
        <>
          <p className="ff-figure">{page.total}</p>
          <p className="ff-muted">
            {page.items.map((a: Account) => a.name).join(" · ")}
          </p>
        </>
      )}
    </DataView>
  );
}

/** First account's projection — safe-to-spend + payday share one
 * read-model call. */
function useFirstProjection() {
  const ready = useHouseholds().data !== null;
  const accounts = useApi(() => api.get("/api/v1/accounts"), ready);
  const first = accounts.data?.items[0]?.id ?? null;
  const projection = useApi(
    () => api.get("/api/v1/accounts/{account_id}/projection",
      { path: { account_id: first! } }),
    first !== null, [first]);
  return projection;
}

function SafeToSpendWidget() {
  const projection = useFirstProjection();
  return (
    <DataView state={projection} empty="No projection yet.">
      {(p: Projection) => (
        <>
          <p className={`ff-figure ${p.below_threshold ? "ff-neg" : "ff-pos"}`}>
            <Money pence={p.safe_to_spend_pence} />
          </p>
          <p className="ff-muted">
            min <Money pence={p.min_balance_pence} />
            {p.min_balance_date
              ? ` on ${formatDate(p.min_balance_date)}` : ""}
          </p>
        </>
      )}
    </DataView>
  );
}

function PaydayWidget() {
  const projection = useFirstProjection();
  return (
    <DataView state={projection} empty="No payday expected.">
      {(p: Projection) => {
        const days = p.next_payday
          ? Math.ceil((Date.parse(p.next_payday) - Date.now())
            / 86_400_000)
          : null;
        return (
          <>
            <p className="ff-figure">
              {days == null ? "—"
                : days <= 0 ? "today" : `${days}d`}
            </p>
            <p className="ff-muted">
              {p.next_payday ? formatDate(p.next_payday) : "not scheduled"}{" "}
              · <Money pence={p.committed_until_payday_pence} /> committed
            </p>
          </>
        );
      }}
    </DataView>
  );
}

function UpcomingWidget() {
  const ready = useHouseholds().data !== null;
  const state = useApi(
    () => api.get("/api/v1/expected-movements", { query: window90() }),
    ready);
  return (
    <DataView state={state} empty="Nothing scheduled.">
      {(moves) => (
        <ul className="ff-list">
          {moves.slice(0, 5).map((m: Movement) => (
            <li key={m.id}>
              <span>{m.label}</span>
              <span className="ff-muted">{formatDate(m.expected_date)}</span>
              <span className={m.amount_pence < 0 ? "ff-neg" : "ff-pos"}>
                <Money pence={m.amount_pence} />
              </span>
            </li>
          ))}
        </ul>
      )}
    </DataView>
  );
}

function SubscriptionsWidget() {
  const ready = useHouseholds().data !== null;
  const state = useApi(
    () => api.get("/api/v1/subscriptions"), ready);
  return (
    <DataView state={state} empty="No subscriptions tracked.">
      {(subs) => (
        <>
          <p className="ff-figure">{subs.length}</p>
          <p className="ff-muted">
            next renewal{" "}
            {subs.length
              ? formatDate(subs
                .map((s: Subscription) => s.next_renewal_date)
                .sort()[0]!)
              : "—"}
          </p>
        </>
      )}
    </DataView>
  );
}

function ForecastWidget() {
  const projection = useFirstProjection();
  return (
    <DataView state={projection} empty="No projection yet.">
      {(p: Projection) => <ProjectionChart projection={p} />}
    </DataView>
  );
}

function ApiStatusWidget() {
  const ready = useHouseholds().data !== null;
  const state = useApi(() => api.get("/api/v1/version"), ready);
  return (
    <DataView state={state} empty="">
      {(v) => <p className="ff-figure">{v["version"]}</p>}
    </DataView>
  );
}

/** The registry — new widgets land here only. */
export const WIDGETS: WidgetDef[] = [
  { id: "accounts", title: "Accounts", Component: AccountsWidget },
  { id: "safe-to-spend", title: "Safe to spend",
    Component: SafeToSpendWidget },
  { id: "payday", title: "Payday", Component: PaydayWidget },
  { id: "upcoming", title: "Upcoming", Component: UpcomingWidget },
  { id: "subscriptions", title: "Subscriptions",
    Component: SubscriptionsWidget },
  { id: "forecast", title: "Balance forecast", Component: ForecastWidget },
  { id: "api-status", title: "API status", roles: ["admin"],
    Component: ApiStatusWidget },
];

export const DEFAULT_ORDER = WIDGETS.map((w) => w.id);

export function widgetById(id: string): WidgetDef | undefined {
  return WIDGETS.find((w) => w.id === id);
}
