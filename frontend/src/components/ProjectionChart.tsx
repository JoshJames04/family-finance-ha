/** Deterministic balance-projection chart (issue #77) — dependency-free
 * SVG: projected balance polyline, zero + threshold lines, min-balance
 * marker. Rendering only; all maths stays server-side. */
import type { components } from "../api/schema";
import { formatPence } from "../lib/format";

type Projection = components["schemas"]["ProjectionOut"];

const W = 720;
const H = 240;
const PAD = { l: 8, r: 8, t: 16, b: 24 };

export function ProjectionChart({ projection }: { projection: Projection }) {
  const days = projection.days;
  if (days.length < 2) return null;
  const first = days[0]!;
  const last = days[days.length - 1]!;
  const balances = days.map((d) => d.balance_pence);
  const threshold = projection.threshold_pence ?? 0;
  const lo = Math.min(0, ...balances);
  const hi = Math.max(0, ...balances, threshold);
  const span = hi - lo || 1;
  const x = (i: number) =>
    PAD.l + (i / (days.length - 1)) * (W - PAD.l - PAD.r);
  const y = (pence: number) =>
    PAD.t + (1 - (pence - lo) / span) * (H - PAD.t - PAD.b);
  const points = days.map((d, i) => `${x(i)},${y(d.balance_pence)}`);
  const minIdx = balances.indexOf(Math.min(...balances));
  const minDay = days[minIdx];
  if (minDay == null) return null;
  const zeroY = y(0);
  const thrY = y(threshold);
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={`Balance projection to ${minDay.date}: minimum ${formatPence(projection.min_balance_pence)}`}
      className="ff-projection-chart"
    >
      <line
        x1={PAD.l}
        x2={W - PAD.r}
        y1={zeroY}
        y2={zeroY}
        className="ff-chart-zero"
      />
      {threshold !== 0 && (
        <line
          x1={PAD.l}
          x2={W - PAD.r}
          y1={thrY}
          y2={thrY}
          className="ff-chart-threshold"
          strokeDasharray="4 3"
        />
      )}
      <polyline
        points={points.join(" ")}
        fill="none"
        className="ff-chart-line"
      />
      <circle
        cx={x(minIdx)}
        cy={y(minDay.balance_pence)}
        r={4}
        className="ff-chart-min"
      >
        <title>
          {`Lowest ${formatPence(minDay.balance_pence)} on ${minDay.date}`}
        </title>
      </circle>
      <text x={PAD.l} y={H - 6} className="ff-chart-axis">
        {first.date}
      </text>
      <text x={W - PAD.r} y={H - 6} textAnchor="end" className="ff-chart-axis">
        {last.date}
      </text>
      <text
        x={x(minIdx)}
        y={y(minDay.balance_pence) - 8}
        textAnchor="middle"
        className="ff-chart-minlabel"
      >
        {formatPence(minDay.balance_pence)}
      </text>
    </svg>
  );
}
