import { api, formatPence } from "../api/client";
import { useApi, useHouseholds } from "../api/useApi";
import { DataView } from "../components/DataView";
import "./pages.css";

function window90(): { start: string; end: string } {
  const start = new Date();
  const end = new Date();
  end.setDate(end.getDate() + 90);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return { start: iso(start), end: iso(end) };
}

export function Dashboard() {
  const households = useHouseholds();
  const ready = households.data !== null;
  const version = useApi(() => api.get("/api/v1/version"), ready);
  const accounts = useApi(() => api.get("/api/v1/accounts"), ready);
  const upcoming = useApi(
    () => api.get("/api/v1/expected-movements", { query: window90() }),
    ready,
  );

  return (
    <section>
      <h1>Dashboard</h1>
      <DataView state={households} empty="No household configured yet.">
        {(hs) => <p className="ff-muted">Household: {hs[0]!.name}</p>}
      </DataView>
      <div className="ff-card-grid">
        <div className="ff-card">
          <h2>Accounts</h2>
          <p className="ff-figure">
            {accounts.data ? accounts.data.total : "—"}
          </p>
        </div>
        <div className="ff-card">
          <h2>Upcoming movements</h2>
          <p className="ff-figure">
            {upcoming.data ? upcoming.data.length : "—"}
          </p>
        </div>
        <div className="ff-card">
          <h2>API</h2>
          <p className="ff-figure">
            {version.data ? version.data["version"] : "unreachable"}
          </p>
        </div>
      </div>
      <h2>Next expected</h2>
      <DataView state={upcoming} empty="Nothing scheduled.">
        {(moves) => (
          <ul className="ff-list">
            {moves.slice(0, 5).map((m) => (
              <li key={m.id}>
                <span>{m.expected_date}</span>
                <span>{m.source_type}</span>
                <span className={m.amount_pence < 0 ? "ff-neg" : "ff-pos"}>
                  {formatPence(m.amount_pence)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </DataView>
    </section>
  );
}
