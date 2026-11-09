/** Cash-flow forecast + financial calendar (issue #77): projected
 * balance chart per account, then a month grid of dated money events —
 * paydays, bills, transfers, renewals — clickable through to the
 * underlying record. */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CalendarDays, ChevronLeft, ChevronRight, TriangleAlert, LineChart } from "lucide-react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { useAccounts, useReady } from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { ProjectionChart } from "../components/ProjectionChart";
import { Panel } from "../components/Panel";
import { Money } from "../components/Money";
import { Button } from "../components/Button";
import { Select } from "../components/Field";
import { IconTile, PageHeader, Segmented, Stat } from "../components/Display";
import { Skeleton } from "../components/Feedback";
import { cn } from "../lib/cn";
import {
  daysUntil, formatDayMonth, formatMonth, formatPence, formatPenceCompact,
  formatWeekdayDayMonth, toISODate,
} from "../lib/format";

type Movement = components["schemas"]["ExpectedMovementOut"];

const SOURCE_ROUTE: Record<string, string> = {
  commitment: "/upcoming",
  payroll_run: "/salary",
  transfer_rule: "/upcoming",
  savings_goal: "/goals",
  subscription: "/subscriptions",
  manual: "/upcoming",
};

const SOURCE_LABEL: Record<string, string> = {
  commitment: "Bill",
  payroll_run: "Payday",
  transfer_rule: "Transfer",
  savings_goal: "Goal",
  subscription: "Renewal",
  manual: "Scheduled",
};

const HORIZONS = [
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "180", label: "6 months" },
] as const;

function monthGrid(year: number, month: number): (string | null)[][] {
  const first = new Date(year, month, 1);
  const days = new Date(year, month + 1, 0).getDate();
  // Monday-first columns
  const offset = (first.getDay() + 6) % 7;
  const cells: (string | null)[] = Array(offset).fill(null);
  for (let d = 1; d <= days; d++) {
    cells.push(`${year}-${String(month + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`);
  }
  while (cells.length % 7) cells.push(null);
  const weeks: (string | null)[][] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  return weeks;
}

export function Forecast() {
  const ready = useReady();
  const accounts = useAccounts();
  const [accountId, setAccountId] = useState<number | null>(null);
  const [horizon, setHorizon] = useState<"30" | "90" | "180">("90");
  const acct = accountId ?? accounts.data?.items.find((a) => !a.archived)?.id ?? accounts.data?.items[0]?.id ?? null;
  const projection = useApi(
    () => api.get("/api/v1/accounts/{account_id}/projection",
      { path: { account_id: acct! }, query: { days: Number(horizon) } }),
    acct !== null,
    [acct, horizon],
  );
  const warnings = useApi(() => api.get("/api/v1/forecast/warnings"), ready);
  const range = useMemo(() => {
    const start = new Date();
    start.setDate(1);
    const end = new Date();
    end.setDate(end.getDate() + 365);
    return { start: toISODate(start), end: toISODate(end) };
  }, []);
  const moves = useApi(
    () => api.get("/api/v1/expected-movements", { query: range }),
    ready,
  );
  const warningList = Array.isArray(warnings.data) ? warnings.data : [];
  const movementList = useMemo(
    () => (Array.isArray(moves.data) ? moves.data : []),
    [moves.data],
  );
  const byDate = useMemo(() => {
    const m = new Map<string, Movement[]>();
    for (const mv of movementList) {
      const k = String(mv.expected_date).slice(0, 10);
      m.set(k, [...(m.get(k) ?? []), mv]);
    }
    return m;
  }, [movementList]);
  const today = new Date();
  const todayIso = toISODate(today);
  const [cursor, setCursor] = useState(
    { y: today.getFullYear(), m: today.getMonth() });
  const [selected, setSelected] = useState<string | null>(null);
  const weeks = useMemo(() => monthGrid(cursor.y, cursor.m), [cursor]);
  const monthName = formatMonth(cursor.y, cursor.m);
  const shift = (n: number) => {
    const d = new Date(cursor.y, cursor.m + n, 1);
    setCursor({ y: d.getFullYear(), m: d.getMonth() });
    setSelected(null);
  };
  const monthTotals = useMemo(() => {
    let inP = 0;
    let outP = 0;
    for (const w of weeks) for (const day of w) {
      for (const e of day ? byDate.get(day) ?? [] : []) {
        if (e.amount_pence >= 0) inP += e.amount_pence; else outP += e.amount_pence;
      }
    }
    return { inP, outP };
  }, [weeks, byDate]);
  const selectedEvents = selected ? byDate.get(selected) ?? [] : [];
  const p = projection.data;
  const accountItems = accounts.data?.items.filter((a) => !a.archived) ?? [];

  return (
    <div>
      <PageHeader
        title="Forecast"
        description="Where your balance is heading, and what's coming up."
        actions={
          <>
            {accountItems.length > 1 && (
              <Select aria-label="Account" value={acct ?? ""} wrapperClassName="w-44"
                onChange={(e) => setAccountId(Number(e.target.value))}>
                {accountItems.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </Select>
            )}
            <Segmented label="Forecast horizon" value={horizon} onChange={setHorizon} options={HORIZONS} />
          </>
        }
      />

      {warningList.map((w) => (
        <div key={w.account_id} role="alert" className="mb-4 flex items-start gap-3 rounded-xl border border-negative/25 bg-negative-soft px-4 py-3 text-[13.5px]">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-negative" />
          <p>
            <b className="font-semibold">{w.name}</b> is projected to drop below its floor on{" "}
            <b className="font-semibold">{formatDayMonth(w.first_dip_date)}</b> — short by{" "}
            <Money pence={Math.abs(w.deficit_pence)} className="font-semibold text-negative" />.
          </p>
        </div>
      ))}

      <DataView state={projection} empty="No projection available" emptyIcon={LineChart}
        emptyDescription="Add an account with an opening balance to forecast it."
        loading={<div className="space-y-4"><Skeleton className="h-24 w-full rounded-xl" /><Skeleton className="h-80 w-full rounded-xl" /></div>}>
        {(proj) => (
          <>
            <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat className="rounded-xl border border-border bg-surface p-4 shadow-card" label="Safe to spend"
                value={<Money pence={proj.safe_to_spend_pence} className={proj.below_threshold ? "text-negative" : ""} />} hint="until next payday" />
              <Stat className="rounded-xl border border-border bg-surface p-4 shadow-card" label="Lowest balance"
                value={<Money pence={proj.min_balance_pence} />} hint={proj.min_balance_date ? `on ${formatDayMonth(proj.min_balance_date)}` : undefined} />
              <Stat className="rounded-xl border border-border bg-surface p-4 shadow-card" label="Next payday"
                value={proj.next_payday ? formatDayMonth(proj.next_payday) : "—"}
                hint={proj.next_payday ? `in ${daysUntil(proj.next_payday)} days` : "Not scheduled"} />
              <Stat className="rounded-xl border border-border bg-surface p-4 shadow-card" label="Committed"
                value={<Money pence={proj.committed_until_payday_pence} />} hint="before payday" />
            </div>
            <Panel title="Projected balance"
              description={proj.threshold_pence ? `Buffer ${formatPence(proj.threshold_pence)} shown dashed` : `Next ${HORIZONS.find((h) => h.value === horizon)?.label}`}
              className="mb-4">
              <ProjectionChart projection={proj} height={300} />
            </Panel>
          </>
        )}
      </DataView>

      <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
        <Panel
          flush
          title={
            <span className="flex items-center gap-1">
              <Button variant="ghost" size="icon-sm" onClick={() => shift(-1)} aria-label="Previous month"><ChevronLeft /></Button>
              <span id="cal-heading" className="min-w-36 text-center">{monthName}</span>
              <Button variant="ghost" size="icon-sm" onClick={() => shift(1)} aria-label="Next month"><ChevronRight /></Button>
            </span>
          }
          actions={
            <span className="hidden text-[12.5px] text-fg-subtle sm:inline">
              In <Money pence={monthTotals.inP} className="font-medium text-positive" /> · Out <Money pence={Math.abs(monthTotals.outP)} className="font-medium text-fg" />
            </span>
          }
        >
          <table className="ff-cal w-full table-fixed border-collapse" aria-labelledby="cal-heading">
            <thead>
              <tr className="h-9 text-[11.5px] font-medium text-fg-subtle">
                {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => (
                  <th key={d} className="font-medium">{d}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {weeks.map((w, i) => (
                <tr key={i}>
                  {w.map((day, j) => {
                    const events = day ? byDate.get(day) ?? [] : [];
                    const net = events.reduce((s, e) => s + e.amount_pence, 0);
                    return (
                      <td key={j} className="border-t border-border p-1 align-top">
                        {day && (
                          <button
                            type="button"
                            className={cn(
                              "ff-cal-day flex h-16 w-full flex-col items-start gap-1 rounded-lg p-1.5 text-left transition-colors sm:h-20",
                              day === selected ? "bg-accent-soft ring-1 ring-accent/40" : "hover:bg-surface-2",
                              day < todayIso && "opacity-50",
                            )}
                            aria-label={`${day} — ${events.length} events`}
                            aria-pressed={day === selected}
                            onClick={() => setSelected(day)}
                          >
                            <span className={cn(
                              "tnum grid size-6 place-items-center rounded-full text-[12px] font-medium",
                              day === todayIso ? "bg-accent text-accent-fg" : "text-fg-muted",
                            )}>
                              {Number(day.slice(8))}
                            </span>
                            {events.length > 0 && (
                              <>
                                <span className="flex gap-0.5">
                                  {events.slice(0, 4).map((e) => (
                                    <span key={e.id} className={cn("size-1.5 rounded-full", e.amount_pence < 0 ? "bg-negative" : "bg-positive")} title={e.label} />
                                  ))}
                                </span>
                                <span className={cn("tnum hidden text-[11px] font-medium sm:block", net < 0 ? "text-fg-muted" : "text-positive")}>
                                  {net > 0 ? "+" : ""}{formatPenceCompact(net)}
                                </span>
                              </>
                            )}
                          </button>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <Panel
          title={selected ? formatWeekdayDayMonth(selected) : "Select a day"}
          description={selected ? `${selectedEvents.length} event${selectedEvents.length === 1 ? "" : "s"}` : "Tap any date to see its money events"}
          flush
          className="h-fit lg:sticky lg:top-20"
        >
          {selected ? (
            <ul className="flex flex-col p-2" aria-label={`Events on ${selected}`}>
              {selectedEvents.map((e) => (
                <li key={e.id}>
                  <Link
                    to={SOURCE_ROUTE[e.source_type] ?? "/upcoming"}
                    className="flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-surface-2"
                  >
                    <IconTile label={e.label} size="sm" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13.5px] font-medium">{e.label}</span>
                      <span className="block text-[12px] text-fg-subtle">{SOURCE_LABEL[e.source_type] ?? e.source_type} · {e.confidence}</span>
                    </span>
                    <Money pence={e.amount_pence} signed tone="auto" className="text-[13.5px] font-medium" />
                  </Link>
                </li>
              ))}
              {selectedEvents.length === 0 && (
                <li className="ff-muted px-2 py-6 text-center text-[13px] text-fg-subtle">No events.</li>
              )}
            </ul>
          ) : (
            <div className="grid place-items-center gap-2 px-4 py-10 text-center text-fg-subtle">
              <CalendarDays className="size-5" />
              <p className="text-[13px]">Paydays, bills, transfers and renewals appear on their dates.</p>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
