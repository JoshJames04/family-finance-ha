/** Cash-flow forecast + financial calendar (issue #77): projected
 * balance chart per account, then a month grid of dated money events —
 * paydays, bills, transfers, renewals — clickable through to the
 * underlying record. */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useApi, useHouseholds } from "../api/useApi";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { ProjectionChart } from "../components/ProjectionChart";
import { Card } from "../components/Card";
import { Money } from "../components/Money";
import { formatMonth, formatPence } from "../lib/format";

type Movement = components["schemas"]["ExpectedMovementOut"];

const SOURCE_ROUTE: Record<string, string> = {
  commitment: "/budget",
  payroll_run: "/accounts",
  transfer_rule: "/transactions",
  savings_goal: "/budget",
  subscription: "/upcoming",
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

function window90() {
  const start = new Date();
  const end = new Date();
  end.setDate(end.getDate() + 90);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return { start: iso(start), end: iso(end) };
}

function monthGrid(year: number, month: number): (string | null)[][] {
  const first = new Date(Date.UTC(year, month, 1));
  const days = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  // Monday-first columns
  const offset = (first.getUTCDay() + 6) % 7;
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
  const households = useHouseholds();
  const accounts = useApi(
    () => api.get("/api/v1/accounts"), households.data !== null);
  const [accountId, setAccountId] = useState<number | null>(null);
  const acct = accountId ?? accounts.data?.items[0]?.id ?? null;
  const projection = useApi(
    () => api.get("/api/v1/accounts/{account_id}/projection",
      { path: { account_id: acct! } }),
    acct !== null,
    [acct],
  );
  const moves = useApi(
    () => api.get("/api/v1/expected-movements", { query: window90() }),
    households.data !== null,
  );
  const byDate = useMemo(() => {
    const m = new Map<string, Movement[]>();
    for (const mv of moves.data ?? []) {
      const k = String(mv.expected_date).slice(0, 10);
      m.set(k, [...(m.get(k) ?? []), mv]);
    }
    return m;
  }, [moves.data]);
  const today = new Date();
  const [cursor, setCursor] = useState(
    { y: today.getFullYear(), m: today.getMonth() });
  const [selected, setSelected] = useState<string | null>(null);
  const weeks = useMemo(() => monthGrid(cursor.y, cursor.m), [cursor]);
  const monthName = formatMonth(cursor.y, cursor.m);
  const shift = (n: number) => {
    const d = new Date(Date.UTC(cursor.y, cursor.m + n, 1));
    setCursor({ y: d.getUTCFullYear(), m: d.getUTCMonth() });
    setSelected(null);
  };
  const p = projection.data;

  return (
    <section>
      <h1>Forecast</h1>
      <label>
        Account{" "}
        <select
          value={acct ?? ""}
          onChange={(e) => setAccountId(Number(e.target.value))}
        >
          {(accounts.data?.items ?? []).map((a) => (
            <option key={a.id} value={a.id}>{a.name}</option>
          ))}
        </select>
      </label>
      <DataView state={projection} empty="No projection available.">
        {(proj) => (
          <>
            <ProjectionChart projection={proj} />
            <div className="ff-card-grid">
              <Card title="Safe to spend">
                <Money pence={proj.safe_to_spend_pence} />
              </Card>
              <Card title="Lowest balance">
                <Money pence={proj.min_balance_pence} />
                <span className="ff-muted">{proj.min_balance_date}</span>
              </Card>
              <Card title="Next payday">
                {proj.next_payday ?? "—"}
              </Card>
            </div>
          </>
        )}
      </DataView>

      <h2 id="cal-heading">
        <button onClick={() => shift(-1)} aria-label="Previous month">‹</button>
        {" "}{monthName}{" "}
        <button onClick={() => shift(1)} aria-label="Next month">›</button>
      </h2>
      <table className="ff-cal" aria-labelledby="cal-heading">
        <thead>
          <tr>{["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(
            (d) => <th key={d}>{d}</th>)}</tr>
        </thead>
        <tbody>
          {weeks.map((w, i) => (
            <tr key={i}>
              {w.map((day, j) => {
                const events = day ? byDate.get(day) ?? [] : [];
                return (
                  <td key={j} className={day === selected ? "ff-cal-sel" : ""}>
                    {day && (
                      <button
                        className="ff-cal-day"
                        aria-label={`${day} — ${events.length} events`}
                        onClick={() => setSelected(day)}
                      >
                        <span className="ff-cal-num">{Number(day.slice(8))}</span>
                        {events.map((e) => (
                          <span
                            key={e.id}
                            className={`ff-cal-dot ${e.amount_pence < 0 ? "ff-neg" : "ff-pos"}`}
                            title={e.label}
                          />
                        ))}
                      </button>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {selected && (
        <ul className="ff-list" aria-label={`Events on ${selected}`}>
          {(byDate.get(selected) ?? []).map((e) => (
            <li key={e.id}>
              <Link to={SOURCE_ROUTE[e.source_type] ?? "/upcoming"}>
                {SOURCE_LABEL[e.source_type] ?? e.source_type} · {e.label}
              </Link>{" "}
              <span className={`ff-num ${e.amount_pence < 0 ? "ff-neg" : "ff-pos"}`}>
                {formatPence(e.amount_pence)}
              </span>
            </li>
          ))}
          {(byDate.get(selected) ?? []).length === 0 && (
            <li className="ff-muted">No events.</li>
          )}
        </ul>
      )}
    </section>
  );
}
