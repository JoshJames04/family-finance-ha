/** Reports: monthly, annual and year-on-year summaries rendered from
 * the reporting engine — per-section figures with prior-period deltas,
 * plus a trailing income-vs-expense trend. */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip,
  XAxis, YAxis,
} from "recharts";
import { BarChart3, FileText } from "lucide-react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { useReady } from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Panel } from "../components/Panel";
import { Money } from "../components/Money";
import { Select } from "../components/Field";
import { PageHeader, Segmented } from "../components/Display";
import { Skeleton } from "../components/Feedback";
import {
  formatMonthName, formatMonthShort, formatPence, formatPenceCompact,
} from "../lib/format";

type Report = components["schemas"]["ReportOut"];
type Figure = components["schemas"]["ReportFigureOut"];
type Mode = "monthly" | "annual" | "yoy";

const now = new Date();
const YEARS = Array.from({ length: 6 }, (_, i) => now.getFullYear() - i);

export function Reports() {
  const ready = useReady();
  const [mode, setMode] = useState<Mode>("monthly");
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const report = useApi(
    () => mode === "monthly"
      ? api.get("/api/v1/reports/monthly", { query: { year, month } })
      : mode === "annual"
        ? api.get("/api/v1/reports/annual", { query: { year } })
        : api.get("/api/v1/reports/yoy", { query: { year } }),
    ready,
    [mode, year, month],
  );
  const trend = useApi(
    () => api.get("/api/v1/analytics/monthly", { query: { months: 12 } }),
    ready,
  );
  const trendData = useMemo(
    () => (trend.data ?? []).map((m) => ({
      month: formatMonthShort(`${m.month}-01`),
      Income: m.income_pence,
      Spending: -m.expense_pence,
    })),
    [trend.data],
  );

  return (
    <div>
      <PageHeader
        title="Reports"
        description="Where the money went — by month, year and against the year before."
        actions={
          <>
            <Segmented
              label="Period"
              value={mode}
              onChange={setMode}
              options={[
                { value: "monthly", label: "Monthly" },
                { value: "annual", label: "Annual" },
                { value: "yoy", label: "Year on year" },
              ]}
            />
            {mode === "monthly" && (
              <Select aria-label="Month" value={String(month)} wrapperClassName="w-32"
                onChange={(e) => setMonth(Number(e.target.value))}>
                {Array.from({ length: 12 }, (_, i) => (
                  <option key={i + 1} value={i + 1}>{formatMonthName(i)}</option>
                ))}
              </Select>
            )}
            <Select aria-label="Year" value={String(year)} wrapperClassName="w-28"
              onChange={(e) => setYear(Number(e.target.value))}>
              {YEARS.map((y) => <option key={y} value={y}>{y}</option>)}
            </Select>
          </>
        }
      />

      {trendData.length > 1 && (
        <Panel title="Income vs spending" description="Last 12 months" className="mb-4">
          <div role="img" aria-label="Income versus spending over the last 12 months" className="h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={trendData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeDasharray="3 4" />
                <XAxis dataKey="month" tickLine={false} axisLine={false} tick={{ fill: "var(--fg-subtle)", fontSize: 11.5 }} dy={6} />
                <YAxis tickFormatter={formatPenceCompact} tickLine={false} axisLine={false} width={56} tick={{ fill: "var(--fg-subtle)", fontSize: 11.5 }} />
                <Tooltip
                  cursor={{ fill: "var(--surface-3)" }}
                  content={({ active, payload, label }) => active && payload?.length ? (
                    <div className="rounded-lg border border-border bg-surface px-3 py-2 text-[12.5px] shadow-lg">
                      <p className="mb-1 text-fg-subtle">{String(label)}</p>
                      {payload.map((p) => (
                        <p key={String(p.dataKey)} className="tnum font-medium" style={{ color: String(p.color) }}>
                          {String(p.dataKey)}: {formatPence(Number(p.value))}
                        </p>
                      ))}
                    </div>
                  ) : null}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} iconSize={9} />
                <Bar dataKey="Income" fill="var(--positive)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
                <Bar dataKey="Spending" fill="var(--chart-line)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      )}

      <DataView
        state={report}
        empty="No report for this period"
        emptyIcon={FileText}
        emptyDescription="Try a different month or year — reports cover periods with recorded activity."
        loading={<div className="grid gap-4 lg:grid-cols-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-48 rounded-xl" />)}</div>}
      >
        {(r: Report) => (
          <div className="grid items-start gap-4 lg:grid-cols-2">
            {r.sections.map((s) => (
              <Panel key={s.key} flush title={s.label}
                actions={s.figures.length
                  ? <Money pence={s.figures.reduce((t, f) => t + Math.abs(f.amount_pence ?? 0), 0)} className="text-[13px] font-semibold" />
                  : undefined}>
                {s.figures.length === 0 ? (
                  <p className="px-4 py-5 text-[13px] text-fg-subtle">No activity this period.</p>
                ) : (
                  <table className="ff-table w-full text-[13.5px]">
                    <tbody>
                      {s.figures.map((f: Figure) => (
                        <tr key={f.key} className="border-b border-border last:border-0 hover:bg-surface-2/60">
                          <td className="py-2.5 pl-4">
                            {f.drill?.endpoint === "/api/v1/transactions" ? (
                              <Link to="/transactions" className="font-medium text-accent hover:underline">{f.label}</Link>
                            ) : (
                              <span className="font-medium">{f.label}</span>
                            )}
                            {f.count != null && <span className="ml-1.5 text-[12px] text-fg-subtle">×{f.count}</span>}
                          </td>
                          <td className="tnum px-3 py-2.5 text-right">
                            {f.amount_pence !== null ? <Money pence={f.amount_pence} /> : "—"}
                          </td>
                          <td className="tnum w-32 px-3 py-2.5 pr-4 text-right text-[12.5px]">
                            {f.prior_amount_pence != null ? (
                              <span className="text-fg-subtle">was <Money pence={f.prior_amount_pence} /></span>
                            ) : null}
                            {f.delta_pence != null && f.delta_pence !== 0 && (
                              <span className="ml-1.5"><Money pence={f.delta_pence} signed tone="variance" /></span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </Panel>
            ))}
            {r.sections.length === 0 && (
              <Panel className="lg:col-span-2">
                <p className="py-4 text-center text-[13.5px] text-fg-subtle">
                  <BarChart3 className="mx-auto mb-2 size-5" />
                  No sections in this report.
                </p>
              </Panel>
            )}
          </div>
        )}
      </DataView>
    </div>
  );
}
