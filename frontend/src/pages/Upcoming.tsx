import { api, formatPence } from "../api/client";
import { useApi, useHouseholds } from "../api/useApi";
import { DataView } from "../components/DataView";

function window90(): { start: string; end: string } {
  const start = new Date();
  const end = new Date();
  end.setDate(end.getDate() + 90);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return { start: iso(start), end: iso(end) };
}

export function Upcoming() {
  const households = useHouseholds();
  const state = useApi(
    () => api.get("/api/v1/expected-movements", { query: window90() }),
    households.data !== null,
  );
  return (
    <section>
      <h1>Upcoming</h1>
      <DataView state={state} empty="Nothing scheduled.">
        {(moves) => (
          <table className="ff-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Source</th>
                <th>Confidence</th>
                <th className="ff-num">Amount</th>
              </tr>
            </thead>
            <tbody>
              {moves.map((m) => (
                <tr key={m.id}>
                  <td>{m.expected_date}</td>
                  <td>{m.source_type}</td>
                  <td className="ff-muted">{m.confidence}</td>
                  <td className={`ff-num ${m.amount_pence < 0 ? "ff-neg" : "ff-pos"}`}>
                    {formatPence(m.amount_pence)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </DataView>
    </section>
  );
}
