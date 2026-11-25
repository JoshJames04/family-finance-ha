/** Balance-projection chart (issue #77): projected balance area, the
 * threshold line and the lowest-balance marker, with a hover tooltip.
 * Rendering only — all maths stays server-side. */
import { useId } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { components } from "../api/schema";
import {
  formatDayMonth,
  formatPence,
  formatPenceCompact,
  formatPenceSigned,
  parseISODate,
  toISODate,
  formatWeekdayDayMonth,
} from "../lib/format";

type Projection = components["schemas"]["ProjectionOut"];

function ChartTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: { date: string; balance: number; movement: number | null } }>;
}) {
  const p = payload?.[0]?.payload;
  if (!active || !p) return null;
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2 text-[12.5px] shadow-lg">
      <p className="text-fg-subtle">{formatWeekdayDayMonth(p.date)}</p>
      <p className="tnum font-semibold text-fg">
        {p.movement === null ? "Opening balance" : "Closing balance"} {formatPence(p.balance)}
      </p>
      {p.movement !== null && (
        <p className="tnum">Net scheduled change {formatPenceSigned(p.movement)}</p>
      )}
    </div>
  );
}

export function ProjectionChart({
  projection,
  height = 220,
  showAxis = true,
}: {
  projection: Projection;
  height?: number;
  showAxis?: boolean;
}) {
  const gid = `bal-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const days = projection.days;
  if (!days.length) return null;
  const opening = parseISODate(days[0]!.date);
  opening.setDate(opening.getDate() - 1);
  const openingDate = toISODate(opening);
  const data = [
    { date: openingDate, balance: projection.start_balance_pence, movement: null },
    ...days.map((day) => ({
      date: day.date, balance: day.balance_pence, movement: day.movements_pence,
    })),
  ];
  const minDate = projection.min_balance_date ?? openingDate;
  const threshold = projection.threshold_pence ?? 0;
  const lo = Math.min(projection.min_balance_pence, threshold);
  const crossesZero = lo < 0;
  return (
    <div
      role="img"
      aria-label={`Balance projection to ${days[days.length - 1]!.date}: lowest ${formatPence(projection.min_balance_pence)} on ${minDate}`}
      className="ff-projection-chart w-full"
      style={{ height }}
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 12, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--chart-line)" stopOpacity={0.28} />
              <stop offset="100%" stopColor="var(--chart-line)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeDasharray="3 4" />
          {showAxis && (
            <XAxis
              dataKey="date"
              tickFormatter={formatDayMonth}
              tickLine={false}
              axisLine={false}
              minTickGap={48}
              tick={{ fill: "var(--fg-subtle)", fontSize: 11.5 }}
              dy={6}
            />
          )}
          {showAxis && (
            <YAxis
              tickFormatter={formatPenceCompact}
              tickLine={false}
              axisLine={false}
              width={56}
              tick={{ fill: "var(--fg-subtle)", fontSize: 11.5 }}
            />
          )}
          <Tooltip
            content={<ChartTooltip />}
            cursor={{ stroke: "var(--border-strong)", strokeWidth: 1 }}
          />
          {threshold > 0 && (
            <ReferenceLine
              y={threshold}
              stroke="var(--warning)"
              strokeDasharray="4 4"
              label={{ value: "Buffer", position: "insideTopLeft", fill: "var(--warning)", fontSize: 11 }}
            />
          )}
          {crossesZero && <ReferenceLine y={0} stroke="var(--negative)" strokeOpacity={0.6} />}
          <Area
            type="monotone"
            dataKey="balance"
            stroke="var(--chart-line)"
            strokeWidth={2}
            fill={`url(#${gid})`}
            activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }}
            isAnimationActive={false}
          />
          <ReferenceDot
            x={minDate}
            y={projection.min_balance_pence}
            r={4.5}
            fill="var(--surface)"
            stroke={projection.min_balance_pence < threshold ? "var(--negative)" : "var(--chart-line)"}
            strokeWidth={2.5}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
