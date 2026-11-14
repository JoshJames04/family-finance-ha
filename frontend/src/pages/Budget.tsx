/** Budget tables and forecast view (issue #125): original / revised /
 * actual / variance columns with accuracy indicators, reason-enforced
 * revision editing, visible lock state, and an expected-movements
 * side panel for the selected period — the money picture at a glance,
 * not a spreadsheet wall. */
import { Fragment, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { CalendarDays, ChevronDown, ChevronLeft, ChevronRight, Lock, Pencil, Target } from "lucide-react";
import { api } from "../api/client";
import { useApi, useMeProfileId } from "../api/useApi";
import {
  pickBudgetFor, useBudgetDetail, useBudgets, useCategories, useReady,
  type Budget as BudgetT, type LineAnalysis,
} from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { SharedPot } from "../components/SharedPot";
import { Badge, type BadgeTone } from "../components/Badge";
import { Money } from "../components/Money";
import { Button } from "../components/Button";
import { Panel } from "../components/Panel";
import { Dialog } from "../components/Dialog";
import { Field, Input, Select } from "../components/Field";
import { IconTile, PageHeader, Progress, Segmented, Stat } from "../components/Display";
import { EmptyState, Skeleton } from "../components/Feedback";
import { cn } from "../lib/cn";
import {
  formatDayMonth, formatDayMonthYear, formatMonthName, formatMonthOf, formatPence,
  formatPercent, formatWeekdayDayMonth, parseISODate,
} from "../lib/format";

type Line = components["schemas"]["BudgetLineOut"];
type Movement = components["schemas"]["ExpectedMovementOut"];
type Cat = components["schemas"]["CategoryOut"];

const STATUS_TONE: Record<string, BadgeTone> = {
  draft: "neutral", approved: "success", locked: "neutral",
};

function ownerKey(b: BudgetT): string {
  return b.owner_profile_id == null ? "shared" : String(b.owner_profile_id);
}

/** The category this line's category rolls up to — its direct parent,
 * or the category itself when it is a top-level grouping bucket (a
 * parent of other categories). Ungrouped lines return null. */
function sectionParent(cat: Cat | undefined, byId: Map<number, Cat>): Cat | null {
  if (!cat) return null;
  if (cat.parent_id != null) return byId.get(cat.parent_id) ?? null;
  // A top-level category that is itself a parent of budgeted categories
  // belongs to its own section.
  if ([...byId.values()].some((c) => c.parent_id === cat.id)) return cat;
  return null;
}

type Section = {
  key: string;
  name: string;
  rows: Line[];
  original: number;
  revised: number;
  actual: number;
  variance: number;
};

/** Group lines into named sections by their categories' parents. When
 * no line has a parent the result is a single flat pseudo-section, so
 * callers can render without headers — zero visual regression. */
function groupLines(
  rows: Line[],
  byId: Map<number, Cat>,
  analyses: Record<string, LineAnalysis>,
): Section[] {
  const groups = new Map<string, { name: string; rows: Line[] }>();
  for (const l of rows) {
    const parent = sectionParent(byId.get(l.category_id), byId);
    const key = parent ? String(parent.id) : "";
    const name = parent?.name ?? "Other categories";
    const g = groups.get(key) ?? { name, rows: [] };
    g.rows.push(l);
    groups.set(key, g);
  }
  const sections = [...groups.entries()].map(([key, g]) => ({
    key,
    name: g.name,
    rows: g.rows,
    original: g.rows.reduce((s, l) => s + l.original_pence, 0),
    revised: g.rows.reduce((s, l) => s + (analyses[String(l.id)]?.effective_pence ?? l.revised_pence ?? l.original_pence), 0),
    actual: g.rows.reduce((s, l) => s + (analyses[String(l.id)]?.actual_pence ?? 0), 0),
    variance: g.rows.reduce((s, l) => s + (analyses[String(l.id)]?.variance_pence ?? 0), 0),
  }));
  // Named sections alphabetically; the ungrouped bucket always last.
  return sections.sort((a, b) =>
    a.key === "" ? 1 : b.key === "" ? -1 : a.name.localeCompare(b.name));
}

/** Collapsed-section state persisted per budget so a period's layout is
 * remembered between visits (issue #272). */
function useCollapsedSections(budgetId: number | null): [Record<string, boolean>, (key: string) => void] {
  const storeKey = `budget-sections:${budgetId ?? "none"}`;
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() => {
    try { return JSON.parse(sessionStorage.getItem(storeKey) ?? "{}"); }
    catch { return {}; }
  });
  useEffect(() => {
    try { setCollapsed(JSON.parse(sessionStorage.getItem(storeKey) ?? "{}")); }
    catch { setCollapsed({}); }
  }, [storeKey]);
  const toggle = (key: string) => {
    setCollapsed((c) => {
      const next = { ...c, [key]: !c[key] };
      try { sessionStorage.setItem(storeKey, JSON.stringify(next)); } catch { /* private mode */ }
      return next;
    });
  };
  return [collapsed, toggle];
}

export function Budget() {
  const ready = useReady();
  const meProfileId = useMeProfileId();
  const budgets = useBudgets();
  const categories = useCategories();
  const profiles = useApi(() => api.get("/api/v1/profiles"), ready);
  const items = useMemo(() => budgets.data?.items ?? [], [budgets.data]);
  const [scope, setScope] = useState("all");
  const [budgetId, setBudgetId] = useState<number | null>(null);
  const [rev, setRev] = useState(0);
  const [revising, setRevising] = useState<Line | null>(null);

  // Default: the budget covering today (shared first), else the most
  // recent one — never a far-future period.
  useEffect(() => {
    if (budgetId !== null || items.length === 0) return;
    const today = pickBudgetFor(items);
    const latestPast = [...items]
      .filter((b) => parseISODate(b.start_date) <= new Date())
      .sort((a, b) => b.start_date.localeCompare(a.start_date))[0];
    setBudgetId((today ?? latestPast ?? items[0])!.id);
  }, [items, budgetId]);

  // Keep a calendar representative when an owner has no budget in that
  // period. Scope changes must not silently select a historical month.
  const selectedBudget = items.find((b) => b.id === budgetId);
  const id = scope === "all" || (selectedBudget && ownerKey(selectedBudget) === scope)
    ? budgetId : null;
  const personal = scope !== "all" && scope !== "shared";
  const single = useBudgetDetail(id, rev, personal ? "pay_cycle" : "calendar");
  const aggregate = scope === "all";
  const combinedRequest = useApi(
    () => api.get("/api/v1/budgets/reports/combined", { query: {
      start: selectedBudget!.start_date, end: selectedBudget!.end_date,
    } }).then(value => ({ id, value })),
    aggregate && selectedBudget !== undefined, [id, aggregate, rev]);
  const combined = combinedRequest.data?.id === id ? combinedRequest.data.value : null;
  const combinedState = { ...combinedRequest,
    loading: combinedRequest.loading || (!combined && !combinedRequest.error) };
  const detail = id === null && selectedBudget
    ? { ...single.detail, data: null, loading: false, error: null }
    : aggregate ? { ...combinedState, data: combined?.detail ?? null } : single.detail;
  const actuals = aggregate ? { ...combinedState, data: combined?.actuals ?? null } : single.actuals;
  const analyses = (actuals.data?.lines ?? {}) as unknown as Record<string, LineAnalysis>;
  const period = detail.data && (!aggregate || combined)
    ? { from: detail.data.period?.start ?? detail.data.start_date,
        to: detail.data.period?.end ?? detail.data.end_date }
    : null;
  const movementRequest = useApi(
    () => api.get("/api/v1/expected-movements",
      { query: { start: period!.from, end: period!.to, budget_id: aggregate ? undefined : id! } }).then((items) => ({ budgetId: id, aggregate, from: period!.from, to: period!.to, items })),
    period !== null, [period?.from, period?.to, id, aggregate]);
  const movements = {
    ...movementRequest,
    data: movementRequest.data?.budgetId === id && movementRequest.data.aggregate === aggregate && movementRequest.data.from === period?.from && movementRequest.data.to === period?.to ? movementRequest.data.items : null,
    loading: movementRequest.loading || ((movementRequest.data?.budgetId !== id || movementRequest.data?.aggregate !== aggregate || movementRequest.data?.from !== period?.from || movementRequest.data?.to !== period?.to) && !movementRequest.error),
  };
  const locked = detail.data
    ? detail.data.status === "locked" || detail.data.read_only
    : false;
  const [collapsed, toggleSection] = useCollapsedSections(id);

  /* ---- period navigation ---- */
  const owners = useMemo(() => [...new Set(items.map(ownerKey))], [items]);
  const owner = scope;
  const ownerItems = owner === "all"
    ? [...new Map([...items].reverse().map(b => [`${b.start_date}:${b.end_date}`, b])).values()]
      .map(b => selectedBudget && b.start_date === selectedBudget.start_date
        && b.end_date === selectedBudget.end_date ? selectedBudget : b)
    : items.filter((b) => ownerKey(b) === owner);
  const year = selectedBudget ? parseISODate(selectedBudget.start_date).getFullYear() : undefined;
  const years = [...new Set([
    ...ownerItems.map((b) => parseISODate(b.start_date).getFullYear()),
    ...(year === undefined ? [] : [year]),
  ])].sort();
  const byMonth = new Map<number, BudgetT>();
  for (const b of ownerItems) {
    const d = parseISODate(b.start_date);
    if (d.getFullYear() === year) byMonth.set(d.getMonth(), b);
  }
  const chrono = [...ownerItems].sort((a, b) => a.start_date.localeCompare(b.start_date));
  const previous = selectedBudget
    ? [...chrono].reverse().find((b) => b.start_date < selectedBudget.start_date) : undefined;
  const next = selectedBudget
    ? chrono.find((b) => b.start_date > selectedBudget.start_date) : undefined;
  const ownerLabel = (k: string) =>
    k === "all" ? "All" : k === "shared" ? "Shared"
      : profiles.data?.items.find((p) => String(p.id) === k)?.display_name ?? `Member ${k}`;
  const switchOwner = (k: string) => {
    setScope(k);
    const cur = selectedBudget ? parseISODate(selectedBudget.start_date) : new Date();
    const candidates = k === "all" ? items : items.filter((b) => ownerKey(b) === k);
    const same = candidates.find((b) => {
      const d = parseISODate(b.start_date);
      return d.getFullYear() === cur.getFullYear() && d.getMonth() === cur.getMonth();
    });
    if (same) setBudgetId(same.id);
  };

  /* ---- totals ---- */
  const lines = detail.data?.lines ?? [];
  const sum = (side: string, f: (l: Line, a?: LineAnalysis) => number) =>
    lines.filter((l) => l.side === side).reduce((s, l) => s + f(l, analyses[String(l.id)]), 0);
  const plannedOut = sum("expense", (l, a) => a?.effective_pence ?? l.revised_pence ?? l.original_pence);
  const actualOut = actuals.data?.spent_pence ?? sum("expense", (_l, a) => a?.actual_pence ?? 0);
  const plannedIn = sum("income", (l, a) => a?.effective_pence ?? l.revised_pence ?? l.original_pence);
  const externalIncome = actuals.data?.external_income_pence ?? actuals.data?.income_received_pence ?? sum("income", (_l, a) => a?.actual_pence ?? 0);
  const sharedFunding = scope === "shared" && actuals.data?.contributions_pence !== undefined;
  const actualIn = sharedFunding ? actuals.data!.contributions_pence : externalIncome;
  const salaryForecasts = (movements.data ?? []).filter((m) =>
    m.amount_pence > 0 && m.outstanding_pence > 0 && m.dismissed_at == null
    && (m.source_type === "employment" || m.source_type === "payroll_run"));
  const plannedSalary = salaryForecasts.reduce((total, m) => total + m.outstanding_pence, 0);

  const sides: Array<{ key: "income" | "expense"; label: string }> = [
    { key: "expense", label: "Expenses" },
    { key: "income", label: "Income plan" },
  ];

  return (
    <div>
      <PageHeader
        title="Budget"
        description="Plan against actuals, month by month."
        actions={
          owners.length > 0 ? (
            <Segmented
              label="Budget owner"
              className="max-w-full flex-wrap [&_button]:h-auto [&_button]:min-h-11 [&_button]:min-w-0 [&_button]:max-w-full [&_button]:wrap-anywhere [&_button]:whitespace-normal"
              value={owner}
              onChange={switchOwner}
              options={["all", ...owners].map((k) => ({ value: k, label: ownerLabel(k) }))}
            />
          ) : undefined
        }
      />

      <DataView
        state={{ ...budgets, data: items.length ? items : budgets.data ? [] : null }}
        empty="No budgets created"
        emptyDescription="Budgets imported from the workbook or created here will appear by month."
        emptyIcon={Target}
        loading={<Skeleton className="h-24 w-full rounded-xl" />}
      >
        {() => (
          <>
            {/* Period picker */}
            <div className="mb-4 flex flex-wrap items-center gap-2 rounded-[var(--radius-card)] border border-border bg-surface p-2 shadow-card">
              <Button variant="ghost" size="icon" aria-label="Previous budget" disabled={!previous}
                onClick={() => previous && setBudgetId(previous.id)}>
                <ChevronLeft />
              </Button>
              <Select
                aria-label="Budget"
                value={id ?? ""}
                sizeVariant="sm"
                wrapperClassName="w-44 sm:hidden"
                onChange={(e) => setBudgetId(Number(e.target.value))}
              >
                {id === null && selectedBudget && (
                  <option value="">{formatMonthOf(selectedBudget.start_date)} — no budget</option>
                )}
                {[...ownerItems].sort((a, b) => b.start_date.localeCompare(a.start_date)).map((b) => (
                  <option key={b.id} value={b.id}>{formatMonthOf(b.start_date)}</option>
                ))}
              </Select>
              <Select
                aria-label="Year"
                value={year}
                sizeVariant="sm"
                wrapperClassName="hidden w-24 sm:block"
                onChange={(e) => {
                  const y = Number(e.target.value);
                  const m = selectedBudget ? parseISODate(selectedBudget.start_date).getMonth() : 0;
                  const inYear = ownerItems.filter((b) => parseISODate(b.start_date).getFullYear() === y);
                  const target = inYear.find((b) => parseISODate(b.start_date).getMonth() === m) ?? inYear[0];
                  if (target) setBudgetId(target.id);
                }}
              >
                {years.map((y) => <option key={y} value={y}>{y}</option>)}
              </Select>
              <div className="hidden min-w-0 flex-1 grid-cols-6 gap-1 sm:grid xl:grid-cols-12" role="group" aria-label="Month">
                {Array.from({ length: 12 }, (_, m) => {
                  const b = byMonth.get(m);
                  const isSel = selectedBudget && parseISODate(selectedBudget.start_date).getMonth() === m;
                  return (
                    <button
                      key={m}
                      type="button"
                      disabled={!b}
                      aria-pressed={!!isSel}
                      onClick={() => b && setBudgetId(b.id)}
                      className={cn(
                        "h-8 min-w-11 flex-1 rounded-lg px-2 text-[12.5px] font-medium transition-colors",
                        isSel ? "bg-fg text-bg" : b ? "text-fg-muted hover:bg-surface-2 hover:text-fg" : "cursor-default text-fg-subtle/40",
                      )}
                    >
                      {formatMonthName(m).slice(0, 3)}
                    </button>
                  );
                })}
              </div>
              <Button variant="ghost" size="icon" aria-label="Next budget" disabled={!next}
                onClick={() => next && setBudgetId(next.id)}>
                <ChevronRight />
              </Button>
            </div>

            <DataView state={detail}
              empty={selectedBudget ? `No budget for this owner in ${formatMonthOf(selectedBudget.start_date)}` : ""}
              loading={<Skeleton className="h-64 w-full rounded-xl" />}>
              {(d) => (
                <>
                  <div className="mb-4 flex flex-wrap items-center gap-2">
                    <h2 className="text-[17px] font-semibold tracking-tight">{formatMonthOf(d.start_date)}</h2>
                    {!aggregate && <Badge tone={STATUS_TONE[d.status] ?? "info"}>{d.status}</Badge>}
                    {locked && (
                      <span className="inline-flex items-center gap-1 text-[12.5px] text-fg-subtle">
                        <Lock className="size-3.5" />
                        <span>{aggregate ? "Combined view — read-only" : "locked — read-only"}</span>
                      </span>
                    )}
                    <span className="ml-auto text-[12.5px] text-fg-subtle">
                      {d.period?.basis === "pay_cycle" ? "Pay cycle: " : ""}
                      {formatDayMonthYear(period?.from ?? d.start_date)} – {formatDayMonthYear(period?.to ?? d.end_date)}
                    </span>
                  </div>

                  {d.period?.warning && <p role="status" className="mb-4 rounded-xl border border-border bg-surface p-4 text-sm">{d.period.warning}</p>}
                  {personal && d.owner_profile_id != null && (
                    <PayCycleSource key={d.owner_profile_id} ownerId={d.owner_profile_id}
                      editable={meProfileId === d.owner_profile_id || profiles.data?.items.some(p => p.id === meProfileId && p.role === "admin") === true}
                      onSaved={() => setRev(r => r + 1)} />
                  )}

                  {/* #415: five distinct measures — planned (fixed
                      for the period), spent (real transactions),
                      left = planned − spent (negative ⇒ overspent),
                      bills remaining (unmatched scheduled occurrences),
                      income received. */}
                  <DataView state={actuals} empty="">
                    {() => <div className="mb-4 grid grid-cols-[repeat(auto-fit,minmax(min(100%,14rem),1fr))] gap-3">
                    {(() => {
                      const left = plannedOut - actualOut;
                      const billsPence = actuals.data?.bills_remaining_pence;
                      const billsCount = actuals.data?.bills_remaining_count;
                      return [
                        { label: "Planned spending", value: <Money pence={plannedOut} />,
                          hint: "approved for this period" },
                        { label: "Spent so far", value: <Money pence={actualOut} />, hint: plannedOut > 0 ? `${formatPercent(actualOut / plannedOut)} of plan used` : undefined },
                        { label: "Left to spend", value: <Money pence={Math.abs(left)} className={left < 0 ? "text-negative" : ""} />,
                          hint: left < 0 ? "overspent" : "of plan left" },
                        ...(billsPence !== undefined ? [{
                          label: "Bills remaining", value: <Money pence={billsPence} />,
                          hint: billsCount === 1 ? "1 scheduled bill unpaid" : `${billsCount ?? 0} scheduled bills unpaid`,
                        }] : []),
                        { label: sharedFunding ? "Net contributions" : "Income received", value: <Money pence={actualIn} />, hint: !sharedFunding && plannedIn > 0 ? `of ${formatPence(plannedIn)} planned` : sharedFunding ? "verified personal funding, net of withdrawals" : undefined },
                        ...(sharedFunding ? [{ label: "Other external income", value: <Money pence={externalIncome} />, hint: "separate from contributions" }] : []),
                        ...(actuals.data?.opening_funds_pence !== undefined ? [{ label: "Opening funds", value: <Money pence={actuals.data.opening_funds_pence} />, hint: actuals.data.opening_as_of ? `Balance through ${formatDayMonthYear(actuals.data.opening_as_of)}; separate from spending plan` : "ledger balance before this period; separate from spending plan" }] : []),
                        ...(scope === "shared" && actuals.data?.unattributed_internal_pence ? [{ label: "Unattributed internal movements", value: <Money pence={actuals.data.unattributed_internal_pence} />, hint: "source unknown; excluded from verified contributions" }] : []),
                        ...(salaryForecasts.length ? [{ label: "Planned salary (forecast)",
                          value: <Money pence={plannedSalary} />, hint: "expected pay; separate from manual income plan" }] : []),
                      ];
                    })().map((s) => (
                      <Stat key={s.label} label={s.label} value={s.value} hint={s.hint}
                        className="rounded-xl border border-border bg-surface p-4 shadow-card" />
                    ))}
                  </div>}
                  </DataView>

                  {aggregate && combined?.sections && (
                    <BudgetSections sections={combined.sections}
                      categoryName={id => categories.byId.get(id)?.name ?? `Category ${id}`} />
                  )}

                  {(actuals.data?.income_items ?? []).length > 0 && (
                    <FundingDetails title={sharedFunding ? "Other external income details" : "Received income details"}
                      caption="Received income" items={actuals.data!.income_items} total={externalIncome}
                      incomePlanCategories={new Set(d.lines.filter(line => line.side === "income").map(line => line.category_id))}
                      incomeSections={aggregate ? combined?.sections : undefined}
                      categoryName={id => categories.byId.get(id)?.name ?? `Category ${id}`} ownerLabel={id => ownerLabel(String(id))} />
                  )}
                  {sharedFunding && (actuals.data?.contribution_items ?? []).length > 0 && (
                    <FundingDetails title="Contribution details" caption="Contributions"
                      items={actuals.data!.contribution_items} total={actualIn} contributions
                      categoryName={id => categories.byId.get(id)?.name ?? `Category ${id}`} ownerLabel={id => ownerLabel(String(id))} />
                  )}

                  {actuals.data?.as_of && (
                    <details className="mb-4 rounded-xl border border-border bg-surface p-4">
                      <summary className="cursor-pointer text-sm font-medium">Reconcile spending</summary>
                      <p className="my-2 text-sm text-fg-muted">
                        Through {formatDayMonth(actuals.data.as_of)}: budget-covered <Money pence={actuals.data.budget_covered_pence ?? 0} />;
                        outside the expense plan <Money pence={actuals.data.unallocated_pence ?? 0} />;
                        including uncategorised <Money pence={actuals.data.uncategorised_pence ?? 0} />.
                        Refunds reduce spending. The table below keeps full-period category actuals.
                      </p>
                      <div className="max-h-72 overflow-auto">
                        <table className="w-full text-left text-sm">
                          <caption className="sr-only">Transactions included in spent so far</caption>
                          <thead><tr><th>Transaction</th><th>Date</th><th>Category</th><th className="text-right">Net spend</th></tr></thead>
                          <tbody>{(actuals.data.spend_items ?? []).map((item, index) => (
                            <tr key={`${item.transaction_id}:${index}`}>
                              <td>#{item.transaction_id}</td><td>{formatDayMonth(item.date)}</td>
                              <td>{item.category_id == null ? "Uncategorised" : categories.byId.get(item.category_id)?.name ?? `Category ${item.category_id}`}</td>
                              <td className="text-right"><Money pence={item.amount_pence} /></td>
                            </tr>
                          ))}</tbody>
                          <tfoot><tr><th colSpan={3}>Spent so far</th><td className="text-right"><Money pence={actualOut} /></td></tr></tfoot>
                        </table>
                      </div>
                    </details>
                  )}

                  <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
                    <div className="flex min-w-0 flex-col gap-4">
                      <DataView state={actuals} empty="" loading={<Skeleton className="h-64 w-full rounded-xl" />}>
                        {() => (
                          <>
                            {sides.map((side) => {
                              const rows = d.lines.filter((l) => l.side === side.key);
                              const showSalary = side.key === "income" && salaryForecasts.length > 0;
                              if (rows.length === 0 && !showSalary) return null;
                              const sections = groupLines(rows, categories.byId, analyses);
                              // Only show section furniture when real grouping
                              // exists — one ungrouped bucket renders flat.
                              const sectioned = sections.some((s) => s.key !== "");
                              const renderLine = (l: Line) => {
                                const an = analyses[String(l.id)];
                                const name = categories.byId.get(l.category_id)?.name ?? `Category ${l.category_id}`;
                                const v = an?.variance_pence ?? 0;
                                const plan = an?.effective_pence ?? l.revised_pence ?? l.original_pence;
                                const actual = (an?.actual_pence ?? 0);
                                const wasRevised = l.revised_pence != null
                                  && l.revised_pence !== l.original_pence;
                                // Usage means nothing when there's no plan and no spend.
                                const used = plan > 0 ? actual / plan : null;
                                const planned = (
                                  <span className="inline-flex flex-col items-end">
                                    <Money pence={plan} className="font-medium" />
                                    {wasRevised && (
                                      <span className="text-[11.5px] text-fg-subtle line-through">
                                        <Money pence={l.original_pence} />
                                      </span>
                                    )}
                                  </span>
                                );
                                return (
                                  <tr key={l.id} className="border-b border-border last:border-0 hover:bg-surface-2/60">
                                    <td className="py-2.5 pl-4" data-label="Category">
                                      <div className="flex items-center gap-2.5">
                                        <IconTile label={name} size="sm" />
                                        <div className="min-w-0">
                                          <p className="font-medium">{name}</p>
                                          {an?.revision_reason && (
                                            <p className="ff-reason truncate text-[12px] text-fg-subtle">
                                              Adjusted: {an.revision_reason}
                                            </p>
                                          )}
                                        </div>
                                      </div>
                                    </td>
                                    <td className="px-3 text-right" data-label="Planned">
                                      {locked ? planned : (
                                        <button
                                          type="button"
                                          className="group ff-inline-edit inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 hover:bg-surface-3"
                                          aria-label={`Adjust ${name}`}
                                          onClick={() => setRevising(l)}
                                        >
                                          {planned}
                                          <Pencil className="size-3 text-fg-subtle opacity-0 group-hover:opacity-100" />
                                        </button>
                                      )}
                                    </td>
                                    <td className="px-3 text-right font-medium" data-label={side.key === "expense" ? "Spent" : "Received"}>
                                      {an != null ? <Money pence={an.actual_pence} /> : "—"}
                                    </td>
                                    <td className="px-3 text-right" data-label="Over/under">
                                      {an != null ? <Money pence={v} tone="variance" signed /> : "—"}
                                    </td>
                                    <td className="w-40 px-3 pr-4" data-label="Used">
                                      {used == null ? <span className="text-fg-subtle">—</span> : (
                                        <div className="flex items-center gap-2">
                                          <Progress value={actual} max={plan} className="flex-1" label={`${name} used`} />
                                          <span className={cn("tnum w-10 text-right text-[12.5px]",
                                            used > 1 ? "text-negative" : "text-fg-muted")}>
                                            {Math.round(used * 100)}%
                                          </span>
                                        </div>
                                      )}
                                    </td>
                                  </tr>
                                );
                              };
                              return (
                                <Panel key={side.key} title={side.label} flush
                                  description={`${rows.length} line${rows.length === 1 ? "" : "s"}`}>
                                  {showSalary && (
                                    <div className="border-b border-border p-4">
                                      <h3 className="text-sm font-medium">Expected salary</h3>
                                      <p className="mt-1 text-[12.5px] text-fg-muted">
                                        Outstanding forecast pay for this period, not received income. Shown separately from manual budget lines, which may already include this salary.
                                      </p>
                                      <ul aria-label="Expected salary" className="mt-2 divide-y divide-border">
                                        {salaryForecasts.map((m) => (
                                          <li key={m.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                                            <div>
                                              <p className="font-medium">{m.label}</p>
                                              <p className="text-[12.5px] text-fg-subtle">{formatWeekdayDayMonth(m.expected_date)}</p>
                                            </div>
                                            <Money pence={m.outstanding_pence} />
                                          </li>
                                        ))}
                                      </ul>
                                    </div>
                                  )}
                                  {rows.length > 0 && <div className="overflow-x-auto">
                                    <table className="ff-table ff-budget-table w-full min-w-[720px] border-collapse text-[13.5px]" aria-label={`${side.label} lines`}>
                                      <thead className="text-left text-[12px] text-fg-subtle">
                                        <tr className="h-9 border-b border-border">
                                          <th className="pl-4 font-medium">Category</th>
                                          <th className="px-3 text-right font-medium">Planned</th>
                                          <th className="px-3 text-right font-medium">{side.key === "expense" ? "Spent" : "Received"}</th>
                                          <th className="px-3 text-right font-medium">Over/under</th>
                                          <th className="w-40 px-3 pr-4 font-medium">Used</th>
                                        </tr>
                                      </thead>
                                      <tbody>
                                        {sections.map((sec) => {
                                          if (!sectioned) {
                                            return <Fragment key={sec.key || "flat"}>{sec.rows.map(renderLine)}</Fragment>;
                                          }
                                          const isCollapsed = !!collapsed[sec.key || "other"];
                                          return (
                                            <Fragment key={sec.key || "other"}>
                                              <tr className="border-b border-border bg-surface-2/50">
                                                <td colSpan={5} className="p-0">
                                                  <button
                                                    type="button"
                                                    aria-expanded={!isCollapsed}
                                                    onClick={() => toggleSection(sec.key || "other")}
                                                    className="flex w-full items-center gap-2.5 px-4 py-2 text-left hover:bg-surface-3/60"
                                                  >
                                                    <ChevronDown className={cn("size-3.5 text-fg-subtle transition-transform", isCollapsed && "-rotate-90")} />
                                                    <IconTile label={sec.name} size="sm" />
                                                    <span className="font-semibold tracking-tight">{sec.name}</span>
                                                    <span className="text-[12px] text-fg-subtle">{sec.rows.length}</span>
                                                    <span className="ml-auto flex items-center gap-4 text-[12.5px]">
                                                      <span className="text-fg-subtle">
                                                        <Money pence={sec.actual} /> of <Money pence={sec.revised || sec.original} />
                                                      </span>
                                                      <Money pence={sec.variance} tone="variance" signed className="w-20 text-right" />
                                                    </span>
                                                  </button>
                                                </td>
                                              </tr>
                                              {!isCollapsed && sec.rows.map(renderLine)}
                                            </Fragment>
                                          );
                                        })}
                                      </tbody>
                                    </table>
                                  </div>}
                                </Panel>
                              );
                            })}
                            {d.lines.length === 0 && salaryForecasts.length === 0 && (
                              <Panel><EmptyState compact icon={Target} title="This budget has no lines yet" /></Panel>
                            )}
                          </>
                        )}
                      </DataView>
                    </div>

                    <Panel title="Expected this period" description="Scheduled movements" flush
                      aria-label="Upcoming in this period" className="h-fit xl:sticky xl:top-20">
                      <div className="p-2">
                        <DataView
                          state={{ ...movements, data: movements.data ?? null }}
                          empty="Nothing expected in this period"
                          emptyIcon={CalendarDays}
                        >
                          {(mvts: Movement[]) => (
                            <ul className="flex flex-col">
                              {mvts.slice(0, 12).map((m) => (
                                <li key={m.id} className="flex items-center gap-3 rounded-lg px-2 py-2">
                                  <IconTile label={m.label} size="sm" />
                                  <div className="min-w-0 flex-1">
                                    <p className="truncate text-[13px] font-medium">{m.label}</p>
                                    <p className="text-[12px] text-fg-subtle">{formatWeekdayDayMonth(m.expected_date)}</p>
                                  </div>
                                  <Money pence={m.amount_pence} signed tone="auto" className="text-[13px]" />
                                </li>
                              ))}
                            </ul>
                          )}
                        </DataView>
                      </div>
                    </Panel>
                  </div>

                  {/* The shared pot is a budgeting decision — it lives
                   * here, period-scoped to the viewed month (issue
                   * #297). */}
                  <div className="mt-4">
                    <SharedPot period={{ start: period?.from ?? d.start_date, end: period?.to ?? d.end_date }} />
                  </div>
                </>
              )}
            </DataView>
          </>
        )}
      </DataView>
      {revising && id !== null && (
        <ReviseForm
          line={revising}
          name={categories.byId.get(revising.category_id)?.name ?? "line"}
          budgetId={id}
          onDone={() => { setRevising(null); setRev((r) => r + 1); toast.success("Budget line revised"); }}
          onCancel={() => setRevising(null)}
        />
      )}
    </div>
  );
}

type FundingItem = components["schemas"]["BudgetFundingItem"];

function FundingDetails({ title, caption, items, total, contributions, incomePlanCategories, incomeSections, categoryName, ownerLabel }: {
  title: string; caption: string; items: FundingItem[]; total: number; contributions?: boolean;
  incomePlanCategories?: ReadonlySet<number>;
  incomeSections?: components["schemas"]["BudgetCombinedSection"][];
  categoryName: (id: number) => string; ownerLabel: (id: number) => string;
}) {
  const key = (item: FundingItem) => `${item.transaction_id}:${item.category_id ?? "uncategorised"}`;
  const plannedItems = incomeSections?.length ? new Set(incomeSections.flatMap(section => {
    const planned = new Set(section.detail?.lines.filter(line => line.side === "income")
      .map(line => line.category_id) ?? []);
    return (section.actuals.income_items ?? [])
      .filter(item => item.category_id != null && planned.has(item.category_id)).map(key);
  })) : undefined;
  const categories = new Map<number | null, { amount: number; planned: number }>();
  for (const item of items) {
    const category = item.category_id ?? null;
    const covered = plannedItems ? plannedItems.has(key(item))
      : category !== null && incomePlanCategories?.has(category);
    const group = categories.get(category) ?? { amount: 0, planned: 0 };
    group.amount += item.amount_pence;
    group.planned += covered ? item.amount_pence : 0;
    categories.set(category, group);
  }
  const planned = [...categories.values()].reduce((sum, group) => sum + group.planned, 0);
  const outside = items.reduce((sum, item) => sum + item.amount_pence, 0) - planned;
  return <details open className="mb-4 rounded-xl border border-border bg-surface p-4">
    <summary className="min-h-11 cursor-pointer text-sm font-medium">{title}</summary>
    <p className="mb-2 text-sm text-fg-muted">Received transactions, including categories outside the manual plan. Forecast pay is shown separately.</p>
    <div className="max-h-72 overflow-auto">
      <table className="w-full min-w-[540px] text-left text-sm" aria-label={caption}>
        <thead><tr><th>Date</th><th>Transaction / payee</th><th>{contributions ? "Contributor" : "Category"}</th><th className="text-right">Amount</th></tr></thead>
        <tbody>{items.map((item, index) => <tr key={`${item.transaction_id}:${index}`} className="border-t border-border">
          <td className="py-2">{formatDayMonthYear(item.date)}</td>
          <td><Link className="inline-flex min-h-11 items-center underline underline-offset-2 hover:text-accent" to={`/transactions?transaction_id=${item.transaction_id}`}>#{item.transaction_id} · {item.payee}</Link></td>
          <td>{contributions ? item.contributor_profile_id == null ? "Unknown contributor" : ownerLabel(item.contributor_profile_id) : item.category_id == null ? "Uncategorised" : categoryName(item.category_id)}</td>
          <td className="text-right"><Money pence={item.amount_pence} /></td>
        </tr>)}</tbody>
        <tfoot><tr><th colSpan={3}>Total {caption.toLowerCase()}</th><td className="text-right"><Money pence={total} /></td></tr></tfoot>
      </table>
    </div>
    {!contributions && <div className="mt-4 overflow-x-auto">
      <table className="w-full text-left text-sm" aria-label="Income by category">
        <thead><tr><th className="pr-2">Category</th><th className="pr-2">Income plan coverage</th><th className="text-right">Received</th></tr></thead>
        <tbody>{[...categories].map(([category, group]) => <tr key={category ?? "uncategorised"} className="border-t border-border">
          <th scope="row" className="py-2 pr-2 font-normal">{category === null ? "Uncategorised" : categoryName(category)}</th>
          <td className="pr-2">{group.planned === 0 ? "Unplanned category" : group.planned === group.amount ? "Planned category" : "Mixed coverage"}</td>
          <td className="text-right"><Money pence={group.amount} /></td>
        </tr>)}</tbody>
        <tfoot>
          <tr><th colSpan={2}>In income plan</th><td className="text-right"><Money pence={planned} /></td></tr>
          <tr><th colSpan={2}>Outside income plan</th><td className="text-right"><Money pence={outside} /></td></tr>
          <tr><th colSpan={2}>Total received income</th><td className="text-right"><Money pence={total} /></td></tr>
        </tfoot>
      </table>
    </div>}
  </details>;
}

function BudgetSections({ sections, categoryName }: {
  sections: components["schemas"]["BudgetCombinedSection"][];
  categoryName: (id: number) => string;
}) {
  return <div className="mb-4 grid gap-4">
    {sections.map(section => {
      const { detail, actuals, owner_profile_id: owner } = section;
      const period = actuals.period;
      const planned = (side: "income" | "expense") => detail?.lines
        .filter(line => line.side === side)
        .reduce((sum, line) => sum + (line.effective_pence ?? line.revised_pence ?? line.original_pence), 0);
      return <Panel key={owner ?? "shared"} title={`${section.name} budget`}
        aria-label={`${section.name} budget`} headingLevel={3}
        description={period ? `${formatDayMonthYear(period.start)} – ${formatDayMonthYear(period.end)} · calendar view · read-only` : "Calendar view · read-only"}>
        {!detail && <p className="mb-3 text-sm text-fg-muted">No plan for this period. Actuals are still included.</p>}
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div><dt className="text-fg-muted">Planned spending</dt><dd>{detail ? <Money pence={planned("expense") ?? 0} /> : "—"}</dd></div>
          <div><dt className="text-fg-muted">Planned income</dt><dd>{detail ? <Money pence={planned("income") ?? 0} /> : "—"}</dd></div>
          <div><dt className="text-fg-muted">Spent so far</dt><dd><Money pence={actuals.spent_pence ?? 0} /></dd></div>
          <div><dt className="text-fg-muted">{owner == null ? "Other external income" : "Income received"}</dt><dd><Money pence={actuals.external_income_pence ?? actuals.income_received_pence ?? 0} /></dd></div>
          {owner == null && <div><dt className="text-fg-muted">Net contributions</dt><dd><Money pence={actuals.contributions_pence ?? 0} /></dd></div>}
        </dl>
        {detail && detail.lines.length > 0 && <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[440px] text-left text-sm" aria-label={`${section.name} budget lines`}>
            <thead><tr><th>Category</th><th>Plan</th><th className="text-right">Planned</th><th className="text-right">Actual</th></tr></thead>
            <tbody>{detail.lines.map(line => <tr key={line.id} className="border-t border-border">
              <th scope="row" className="py-2 font-normal">{categoryName(line.category_id)}</th>
              <td>{line.side === "income" ? "Income" : "Expense"}</td>
              <td className="text-right"><Money pence={line.effective_pence ?? line.revised_pence ?? line.original_pence} /></td>
              <td className="text-right"><Money pence={actuals.totals[String(line.id)] ?? 0} /></td>
            </tr>)}</tbody>
          </table>
        </div>}
      </Panel>;
    })}
  </div>;
}

function PayCycleSource({ ownerId, editable, onSaved }: { ownerId: number; editable: boolean; onSaved: () => void }) {
  const source = useApi(() => api.get("/api/v1/budgets/pay-cycle-settings", { query: { owner_profile_id: ownerId } }), true, [ownerId]);
  const [selection, setSelection] = useState<string | null>(null);
  const [dayInput, setDayInput] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const value = selection ?? (source.data?.employment_id != null ? String(source.data.employment_id) : source.data?.payday_day != null ? "fixed" : "");
  const day = dayInput ?? (source.data?.payday_day == null ? "" : String(source.data.payday_day));
  const parsedDay = Number(day);
  const validDay = Number.isInteger(parsedDay) && parsedDay >= 1 && parsedDay <= 31;
  const save = async () => {
    if (value === "fixed" && !validDay) return;
    setSaving(true); setError(null);
    try {
      await api.put("/api/v1/budgets/pay-cycle-settings", { body: value === "fixed"
        ? { owner_profile_id: ownerId, employment_id: null, payday_day: parsedDay }
        : { owner_profile_id: ownerId, employment_id: value === "" ? null : Number(value) } });
      source.refetch(); onSaved(); toast.success("Pay-cycle source saved");
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to save pay-cycle source"); }
    finally { setSaving(false); }
  };
  return <div className="mb-4 rounded-xl border border-border bg-surface p-4">
    <DataView state={source} empty="No pay-cycle sources available">
      {data => <div className="flex flex-wrap items-end gap-3">
        <Field label="Pay-cycle source" className="min-w-0 flex-1" hint="Automatic requires one monthly employment source. You can also set a fixed monthly payday.">
          {id => <Select id={id} aria-label="Pay-cycle source" value={value} onChange={e => setSelection(e.target.value)} disabled={!editable || saving} className="min-h-11">
            <option value="">Automatic</option>
            <option value="fixed">Fixed monthly payday</option>
            {(data.options ?? []).map(option => <option key={option.employment_id} value={option.employment_id}>{option.employer}</option>)}
          </Select>}
        </Field>
        {value === "fixed" && <Field label="Monthly payday" className="min-w-0 flex-1" hint="Saturday or Sunday paydays move to the previous Friday.">
          {id => <Input id={id} aria-label="Monthly payday" type="number" min={1} max={31} step={1}
            value={day} onChange={e => setDayInput(e.target.value)} disabled={!editable || saving} className="min-h-11" />}
        </Field>}
        {editable && <Button className="min-h-11" disabled={saving || source.loading || (value === "fixed" && !validDay)} onClick={() => void save()}>{saving ? "Saving…" : "Save source"}</Button>}
      </div>}
    </DataView>
    {error && <p role="alert" className="mt-2 text-sm text-negative">{error}</p>}
  </div>;
}

/** Revision form — the API requires a reason; the UI enforces it the
 * same way (issue #125). Amount entered in pounds, sent as Money. */
function ReviseForm({ line, name, budgetId, onDone, onCancel }: {
  line: Line;
  name: string;
  budgetId: number;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const [pounds, setPounds] = useState(
    String((line.revised_pence ?? line.original_pence) / 100));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    api.patch("/api/v1/budgets/{budget_id}/lines/{line_id}", {
      path: { budget_id: budgetId, line_id: line.id },
      body: { amount: pounds, reason },
    }).then(onDone).catch((err) => {
      setBusy(false);
      setError(err instanceof Error ? err.message : "Revision failed");
    });
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => { if (!o) onCancel(); }}
      title={`Revise ${name}`}
      description={<>Originally planned at {formatPence(line.original_pence)}. Revisions are kept alongside the original for accuracy tracking.</>}
      footer={
        <>
          <Button variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button variant="primary" type="submit" form="ff-revise" disabled={busy || !reason.trim()}>
            {busy ? "Saving…" : "Save revision"}
          </Button>
        </>
      }
    >
      <form id="ff-revise" aria-label="Revise budget line" onSubmit={submit} className="grid gap-4">
        <Field label="New amount (£)">
          {(id) => (
            <Input id={id} required type="number" step="0.01" min="0" aria-label="Revised amount"
              value={pounds} onChange={(e) => setPounds(e.target.value)} />
          )}
        </Field>
        <Field label="Reason (required)" hint="e.g. tariff change, one-off cost">
          {(id) => (
            <Input id={id} required aria-label="Revision reason" autoFocus
              value={reason} onChange={(e) => setReason(e.target.value)} />
          )}
        </Field>
        {error && <p role="alert" className="text-[13px] text-negative">{error}</p>}
      </form>
    </Dialog>
  );
}
