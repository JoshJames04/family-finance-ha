/** Dashboard widget registry (issue #123): each entry is a component
 * reading the read-model APIs — no bespoke data paths. New widgets
 * are added here only; the dashboard framework (order/hidden/layout
 * persistence) needs no change. `span` sets the desktop column width. */
import { useState, type ReactElement } from "react";
import { Link } from "react-router-dom";
import {
  ArrowDownLeft, ArrowRight, ArrowUpRight, CalendarDays, Lock,
  PiggyBank, Repeat, TriangleAlert, Wallet,
} from "lucide-react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import type { components } from "../api/schema";
import {
  pickBudgetFor, reportFigure, reportSectionTotal, useAccounts, useBudgetDetail,
  useBudgets, useCategories, useMonthlyReport, useProjection, useReady, useUpcoming,
} from "../api/hooks";
import { Select } from "../components/Field";
import { DataView } from "../components/DataView";
import { HeroMoney, Money } from "../components/Money";
import { Panel } from "../components/Panel";
import { Badge } from "../components/Badge";
import { IconTile, Progress, Ring, Stat } from "../components/Display";
import { ErrorState, Skeleton } from "../components/Feedback";
import { ProjectionChart } from "../components/ProjectionChart";
import { useShell } from "../shell/ShellContext";
import {
  daysUntil, formatDayMonth, formatMonthOf, formatPence, formatPenceWhole,
  formatPercent, formatRelativeDays, formatSmartDate, formatWeekdayDayMonth,
} from "../lib/format";

type Role = components["schemas"]["Role"];
type Projection = components["schemas"]["ProjectionOut"];

export interface WidgetDef {
  id: string;
  title: string;
  /** Roles the widget is available to — omit for everyone. */
  roles?: Role[];
  span: 1 | 2 | 3;
  Component: (props: { title: string }) => ReactElement;
}

const viewAll = (to: string, label = "View all") => (
  <Link to={to} className="inline-flex items-center gap-1 text-[12.5px] font-medium text-fg-muted hover:text-fg">
    {label}<ArrowRight className="size-3.5" />
  </Link>
);

/* ---------------- hero ---------------- */

function SafeToSpendWidget({ title }: { title: string }) {
  const accounts = useAccounts();
  const [accountId, setAccountId] = useState<number | null>(null);
  const projection = useProjection(accountId);
  const p = projection.data as Projection | null;
  const days = p?.next_payday ? Math.max(1, daysUntil(p.next_payday)) : null;
  return (
    <section className="bg-hero relative flex h-full min-h-[210px] flex-col overflow-hidden rounded-[18px] p-6 text-white shadow-card sm:p-7">
      <div aria-hidden className="pointer-events-none absolute -top-16 -right-16 size-64 rounded-full bg-white/10" />
      <div aria-hidden className="pointer-events-none absolute -right-4 -bottom-24 size-48 rounded-full bg-white/[0.06]" />
      <h2 className="relative text-[14px] font-medium text-white/85">{title}</h2>
      {(accounts.data?.items.filter((a) => !a.archived).length ?? 0) > 1 && (
        <Select aria-label="Safe to spend account" value={accountId ?? accounts.data?.items.find((a) => !a.archived)?.id ?? ""}
          onChange={(e) => setAccountId(Number(e.target.value))} wrapperClassName="relative mt-2 max-w-64">
          {accounts.data?.items.filter((a) => !a.archived).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </Select>
      )}
      {projection.accountName && <p className="relative mt-1 text-[13px] text-white/85">{projection.accountName}</p>}
      {projection.loading ? (
        <div className="relative mt-3 space-y-3" role="status" aria-label="Loading">
          <div className="h-12 w-56 animate-pulse rounded-lg bg-white/20" />
          <div className="h-4 w-64 animate-pulse rounded bg-white/15" />
        </div>
      ) : projection.error && !p ? (
        // Failed with nothing to show — never fall through to the
        // "add an account" CTA or a fabricated £0 (#460).
        <div className="relative mt-3" role="alert">
          <p className="text-[15px] text-white/90">Couldn't load this — {projection.error}</p>
          <button type="button" onClick={projection.refetch}
            className="mt-4 inline-flex h-9 items-center rounded-lg bg-white px-3.5 text-[13.5px] font-semibold text-zinc-900">
            Retry
          </button>
        </div>
      ) : !p ? (
        <div className="relative mt-3">
          <p className="text-[15px] text-white/90">Add an account to see what's safe to spend.</p>
          <Link to="/accounts" className="mt-4 inline-flex h-9 items-center gap-2 rounded-lg bg-white px-3.5 text-[13.5px] font-semibold text-zinc-900">
            Set up accounts <ArrowRight className="size-4" />
          </Link>
        </div>
      ) : (
        <>
          <p className="relative mt-1.5">
            <HeroMoney pence={p.safe_to_spend_pence} className="text-[44px] leading-none sm:text-[54px]" fractionClassName="text-[26px] sm:text-[30px]" />
          </p>
          <p className="relative mt-2.5 text-[14px] text-white/90">
            {days
              ? <>About <b className="tnum font-semibold">{formatPenceWhole(Math.floor(p.safe_to_spend_pence / days))} a day</b> for the next {days} day{days === 1 ? "" : "s"}</>
              : <>Balance {formatPence(p.start_balance_pence)} · No payday scheduled for {projection.accountName ?? "this account"}</>}
          </p>
          <div className="relative mt-auto flex flex-wrap gap-2 pt-5">
            {p.next_payday ? (
              <span className="inline-flex h-8 items-center gap-1.5 rounded-full bg-black/20 px-3 text-[12.5px] font-medium">
                <CalendarDays className="size-3.5" /> Payday {formatDayMonth(p.next_payday)}
              </span>
            ) : (
              <Link to="/salary" className="inline-flex h-8 items-center gap-1.5 rounded-full bg-white/95 px-3 text-[12.5px] font-semibold text-zinc-900">
                <CalendarDays className="size-3.5" /> Review salary settings
              </Link>
            )}
            <span className="inline-flex h-8 items-center gap-1.5 rounded-full bg-black/20 px-3 text-[12.5px] font-medium">
              <Lock className="size-3.5" /> <span className="tnum">{formatPenceWhole(p.committed_until_payday_pence)}</span> committed
            </span>
            {p.below_threshold && (
              <span className="inline-flex h-8 items-center gap-1.5 rounded-full bg-white px-3 text-[12.5px] font-semibold text-rose-700">
                <TriangleAlert className="size-3.5" /> Dips below buffer {p.min_balance_date ? formatDayMonth(p.min_balance_date) : ""}
              </span>
            )}
          </div>
        </>
      )}
    </section>
  );
}

/* ---------------- budget ring ---------------- */

function useCurrentBudget() {
  const budgets = useBudgets();
  const current = pickBudgetFor(budgets.data?.items ?? []);
  const { detail, actuals, analyses } = useBudgetDetail(current?.id ?? null);
  const expenseLines = (detail.data?.lines ?? []).filter((l) => l.side === "expense");
  const planned = expenseLines.reduce((s, l) => s + (analyses[String(l.id)]?.effective_pence ?? l.revised_pence ?? l.original_pence), 0);
  const spent = expenseLines.reduce((s, l) => s + Math.abs(analyses[String(l.id)]?.actual_pence ?? 0), 0);
  const loading = budgets.loading || !!current && (detail.loading || actuals.loading);
  const error = budgets.error ?? (current ? detail.error ?? actuals.error : null);
  const refetch = () => { budgets.refetch(); if (current) { detail.refetch(); actuals.refetch(); } };
  return { budgets, current, detail, actuals, analyses, expenseLines, planned, spent, loading, error, refetch };
}

function BudgetRingWidget({ title }: { title: string }) {
  const { current, planned, spent, loading, error: failed, refetch } = useCurrentBudget();
  const ratio = planned > 0 ? spent / planned : 0;
  return (
    <Panel title={title} description={current ? formatMonthOf(current.start_date) : undefined} actions={viewAll("/budget", "Open")} className="h-full">
      {loading ? (
        <div className="grid place-items-center py-4"><Skeleton className="size-[132px] rounded-full" /></div>
      ) : failed ? (
        // API failure must not render "No budget covers this
        // month" — a real budget may exist (#460).
        <ErrorState message={failed} onRetry={refetch} />
      ) : !current ? (
        <div className="py-6 text-center text-[13px] text-fg-subtle">No budget covers this month yet.</div>
      ) : (
        <div className="flex flex-col items-center gap-3 py-1">
          <Ring ratio={ratio} size={132} stroke={11}>
            <div>
              <p className="tnum text-[26px] font-semibold tracking-tight">{formatPercent(ratio)}</p>
              <p className="text-[11.5px] text-fg-subtle">used</p>
            </div>
          </Ring>
          <p className="text-center text-[13px] text-fg-muted">
            <Money pence={Math.max(0, planned - spent)} className="font-semibold text-fg" /> left of <Money pence={planned} />
          </p>
        </div>
      )}
    </Panel>
  );
}

/* ---------------- this month KPIs ---------------- */

function MonthKpisWidget({ title }: { title: string }) {
  const now = new Date();
  const { dataVersion } = useShell();
  const report = useMonthlyReport(now.getFullYear(), now.getMonth() + 1, dataVersion);
  const subs = useApi(() => api.get("/api/v1/subscriptions/annualised"), useReady());
  const r = report.data;
  const income = reportFigure(r, "income", "income_total");
  const spent = reportSectionTotal(r, "spend_by_category");
  const saved = reportFigure(r, "savings", "savings_pence");
  const delta = (cur?: number | null, prior?: number | null) =>
    cur != null && prior ? (cur - prior) / Math.abs(prior) : null;
  const cell = "rounded-xl border border-border bg-surface p-4 shadow-card";
  return (
    <section aria-label={title} className="h-full">
      <h2 className="sr-only">{title}</h2>
      <div className="grid h-full grid-cols-2 gap-3 lg:grid-cols-4">
        <DataView state={report} empty="No monthly report" loading={<div className={cell}><Skeleton className="h-20" /></div>}>
          {() => <>
            <Stat className={cell} label="Income this month" icon={ArrowDownLeft} iconClassName="bg-positive-soft text-positive"
              value={<Money pence={income?.amount_pence ?? 0} />}
              delta={delta(income?.amount_pence, income?.prior_amount_pence)} hint="vs last month" />
            <Stat className={cell} label="Spent this month" icon={ArrowUpRight} iconClassName="bg-negative-soft text-negative"
              value={<Money pence={spent} />} hint={r ? undefined : "No data"} />
            <Stat className={cell} label="Net saved" icon={PiggyBank} iconClassName="bg-accent-soft text-accent"
              value={<Money pence={saved?.amount_pence ?? 0} />}
              delta={delta(saved?.amount_pence, saved?.prior_amount_pence)} hint="vs last month" />
          </>}
        </DataView>
        <DataView state={subs} empty="No subscription report" loading={<div className={cell}><Skeleton className="h-20" /></div>}>
          {() => <Stat className={cell} label="Subscriptions" icon={Repeat} iconClassName="bg-warning-soft text-warning"
            value={<Money pence={Math.round((subs.data?.total_annualised_pence ?? 0) / 12)} />} hint="per month" />}
        </DataView>
      </div>
    </section>
  );
}

/* ---------------- forecast ---------------- */

function ForecastWidget({ title }: { title: string }) {
  const projection = useProjection();
  return (
    <Panel
      title={title}
      description={projection.data ? <>{projection.accountName} · Lowest <Money pence={projection.data.min_balance_pence} className="font-medium text-fg" />{projection.data.min_balance_date ? ` on ${formatDayMonth(projection.data.min_balance_date)}` : ""}</> : undefined}
      actions={viewAll("/forecast", "Details")}
      className="h-full"
    >
      <DataView state={projection} empty="No projection yet." emptyDescription="Add an account with a balance to forecast." loading={<Skeleton className="h-[220px] w-full" />}>
        {(p: Projection) => <ProjectionChart projection={p} />}
      </DataView>
    </Panel>
  );
}

/* ---------------- upcoming ---------------- */

function UpcomingWidget({ title }: { title: string }) {
  const { dataVersion } = useShell();
  const state = useUpcoming(30, dataVersion);
  return (
    <Panel title={title} description="Next 30 days" actions={viewAll("/upcoming")} flush className="h-full">
      <div className="p-2">
        <DataView state={state} empty="Nothing scheduled" emptyDescription="Bills, transfers and paydays you add will appear here." emptyIcon={CalendarDays}>
          {(moves) => (
            <ul className="flex flex-col">
              {moves.slice(0, 6).map((m) => (
                <li key={m.id} className="flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-surface-2">
                  <IconTile label={m.label} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13.5px] font-medium">{m.label}</p>
                    <p className="text-[12px] text-fg-subtle">{formatWeekdayDayMonth(m.expected_date)} · {formatRelativeDays(m.expected_date)}</p>
                  </div>
                  <Money pence={m.amount_pence} signed tone="auto" className="text-[13.5px] font-medium" />
                </li>
              ))}
            </ul>
          )}
        </DataView>
      </div>
    </Panel>
  );
}

/* ---------------- budget categories ---------------- */

function BudgetCategoriesWidget({ title }: { title: string }) {
  const { current, detail, actuals, analyses, expenseLines, loading, error, refetch } = useCurrentBudget();
  const cats = useCategories();
  const rows = expenseLines
    .map((l) => {
      const a = analyses[String(l.id)];
      const plan = a?.effective_pence ?? l.revised_pence ?? l.original_pence;
      return { id: l.id, name: cats.byId.get(l.category_id)?.name ?? `Category ${l.category_id}`, plan, spent: Math.abs(a?.actual_pence ?? 0) };
    })
    .filter((r) => r.plan > 0 || r.spent > 0)
    .sort((a, b) => b.plan - a.plan)
    .slice(0, 7);
  return (
    <Panel title={title} description={current ? formatMonthOf(current.start_date) : undefined} actions={viewAll("/budget")} flush className="h-full">
      <DataView state={{ ...detail, data: current && actuals.data ? detail.data : null, loading: loading || !!current && cats.loading, error: error ?? cats.error, refetch: () => { refetch(); cats.refetch(); } }} empty="No budget for this month">
        {() => (
          <ul className="divide-y divide-border">
            {rows.map((r) => (
              <li key={r.id} className="grid grid-cols-[auto_1fr_auto] items-center gap-x-3 gap-y-1.5 px-4 py-2.5">
                <IconTile label={r.name} size="sm" className="row-span-2" />
                <p className="truncate text-[13.5px] font-medium">{r.name}</p>
                <p className="tnum text-right text-[12.5px] text-fg-muted">
                  <span className={r.spent > r.plan ? "font-medium text-negative" : "text-fg"}>{formatPenceWhole(r.spent)}</span> / {formatPenceWhole(r.plan)}
                </p>
                <Progress value={r.spent} max={r.plan} className="col-span-2" label={`${r.name} budget used`} />
              </li>
            ))}
          </ul>
        )}
      </DataView>
    </Panel>
  );
}

/* ---------------- recent transactions ---------------- */

function RecentWidget({ title }: { title: string }) {
  const { dataVersion } = useShell();
  const state = useApi(() => api.get("/api/v1/transactions", { query: { size: 6 } }), useReady(), [dataVersion]);
  return (
    <Panel title={title} actions={viewAll("/transactions")} flush className="h-full">
      <div className="p-2">
        <DataView state={{ ...state, data: state.data?.items ?? null }} empty="No transactions yet" emptyIcon={Wallet}>
          {(items) => (
            <ul className="flex flex-col">
              {items.map((t) => (
                <li key={t.id} className="flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-surface-2">
                  <IconTile label={t.payee} size="sm" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13.5px] font-medium">{t.payee}</p>
                    <p className="text-[12px] text-fg-subtle">{formatSmartDate(t.date)}</p>
                  </div>
                  <Money pence={t.amount_pence} signed tone="auto" className="text-[13.5px] font-medium" />
                </li>
              ))}
            </ul>
          )}
        </DataView>
      </div>
    </Panel>
  );
}

/* ---------------- accounts ---------------- */

function AccountBalance({ id }: { id: number }) {
  const bal = useApi(() => api.get("/api/v1/accounts/{account_id}/balance", { path: { account_id: id } }), true, [id]);
  const pence = (bal.data as { balance_pence?: number } | null)?.balance_pence;
  if (bal.loading) return <Skeleton className="h-4 w-16" />;
  // A failed balance read must not mimic "no data" (#460) — offer
  // a retry affordance rather than a silent em-dash.
  if (bal.error && pence == null)
    return <button type="button" onClick={bal.refetch}
      className="text-[11.5px] font-medium text-negative underline underline-offset-2"
      title={bal.error}>Retry</button>;
  return pence == null ? <span className="text-fg-subtle">—</span> : <Money pence={pence} className="font-medium" />;
}

function AccountsWidget({ title }: { title: string }) {
  const state = useAccounts();
  return (
    <Panel title={title} actions={viewAll("/accounts")} flush className="h-full">
      <div className="p-2">
        <DataView state={{ ...state, data: state.data?.items.filter((a) => !a.archived) ?? null }} empty="No accounts yet" emptyIcon={Wallet}
          emptyAction={<Link to="/accounts" className="text-[13px] font-medium text-accent">Add an account</Link>}>
          {(items) => (
            <ul className="flex flex-col">
              {items.map((a) => (
                <li key={a.id} className="flex items-center gap-3 rounded-lg px-2 py-2">
                  <IconTile label={a.type} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13.5px] font-medium">{a.name}</p>
                    <p className="text-[12px] capitalize text-fg-subtle">{a.type.replace(/_/g, " ")}</p>
                  </div>
                  <AccountBalance id={a.id} />
                </li>
              ))}
            </ul>
          )}
        </DataView>
      </div>
    </Panel>
  );
}

/* ---------------- subscriptions ---------------- */

function SubscriptionsWidget({ title }: { title: string }) {
  const state = useApi(() => api.get("/api/v1/subscriptions"), useReady());
  return (
    <Panel title={title} actions={viewAll("/subscriptions")} flush className="h-full">
      <div className="p-2">
        <DataView state={state} empty="No subscriptions tracked" emptyIcon={Repeat} emptyDescription="Detected recurring payments show up in Review.">
          {(subs) => (
            <ul className="flex flex-col">
              {[...subs].filter((s) => s.status === "active").sort((a, b) => a.next_renewal_date.localeCompare(b.next_renewal_date)).slice(0, 5).map((s) => (
                <li key={s.id} className="flex items-center gap-3 rounded-lg px-2 py-2">
                  <IconTile label={s.payee} size="sm" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13.5px] font-medium">{s.payee}</p>
                    <p className="text-[12px] text-fg-subtle">Renews {formatDayMonth(s.next_renewal_date)}</p>
                  </div>
                  <Money pence={s.expected_pence} className="text-[13.5px]" />
                </li>
              ))}
            </ul>
          )}
        </DataView>
      </div>
    </Panel>
  );
}

/* ---------------- system (admin) ---------------- */

function SystemWidget({ title }: { title: string }) {
  const diag = useApi(() => api.get("/api/v1/diagnostics"), useReady());
  const d = diag.data as Record<string, Record<string, unknown>> | null;
  return (
    <Panel title={title} actions={viewAll("/settings", "Settings")} className="h-full">
      <DataView state={diag} empty="">
        {() => (
          <dl className="grid grid-cols-2 gap-3 text-[13px]">
            <div><dt className="text-fg-subtle">Version</dt><dd className="font-medium">{String(d?.["app"]?.["version"] ?? "—")}</dd></div>
            <div><dt className="text-fg-subtle">Database</dt><dd><Badge tone={d?.["database"]?.["ok"] === false ? "error" : "success"}>{d?.["database"]?.["ok"] === false ? "Issue" : "Healthy"}</Badge></dd></div>
          </dl>
        )}
      </DataView>
    </Panel>
  );
}

/** The registry — new widgets land here only. */
export const WIDGETS: WidgetDef[] = [
  { id: "safe-to-spend", title: "Safe to spend", span: 2, Component: SafeToSpendWidget },
  { id: "budget", title: "This month's budget", span: 1, Component: BudgetRingWidget },
  { id: "kpis", title: "This month", span: 3, Component: MonthKpisWidget },
  { id: "forecast", title: "Balance forecast", span: 2, Component: ForecastWidget },
  { id: "upcoming", title: "Upcoming", span: 1, Component: UpcomingWidget },
  { id: "budget-categories", title: "Budget by category", span: 2, Component: BudgetCategoriesWidget },
  { id: "recent", title: "Recent transactions", span: 1, Component: RecentWidget },
  { id: "accounts", title: "Accounts", span: 1, Component: AccountsWidget },
  { id: "subscriptions", title: "Subscriptions", span: 1, Component: SubscriptionsWidget },
  { id: "system", title: "System status", roles: ["admin"], span: 1, Component: SystemWidget },
];

export const DEFAULT_ORDER = WIDGETS.map((w) => w.id);

export function widgetById(id: string): WidgetDef | undefined {
  return WIDGETS.find((w) => w.id === id);
}
