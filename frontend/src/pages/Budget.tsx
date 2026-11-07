import { api } from "../api/client";
import { useApi, useHouseholds } from "../api/useApi";
import { DataView } from "../components/DataView";

export function Budget() {
  const households = useHouseholds();
  const state = useApi(
    () => api.get("/api/v1/budgets"),
    households.data !== null,
  );
  return (
    <section>
      <h1>Budgets</h1>
      <DataView
        state={{ ...state, data: state.data?.items ?? null }}
        empty="No budgets created."
      >
        {(items) => (
          <ul className="ff-list">
            {items.map((b) => (
              <li key={b.id}>
                <span>{b.name}</span>
                <span className="ff-muted">
                  {b.start_date} → {b.end_date}
                </span>
                <span className="ff-muted">{b.status}</span>
              </li>
            ))}
          </ul>
        )}
      </DataView>
    </section>
  );
}
