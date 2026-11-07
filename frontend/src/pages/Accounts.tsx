import { api } from "../api/client";
import { useApi, useHouseholds } from "../api/useApi";
import { DataView } from "../components/DataView";

export function Accounts() {
  const households = useHouseholds();
  const state = useApi(
    () => api.get("/api/v1/accounts"),
    households.data !== null,
  );
  return (
    <section>
      <h1>Accounts</h1>
      <DataView
        state={{ ...state, data: state.data?.items ?? null }}
        empty="No accounts yet."
      >
        {(items) => (
          <ul className="ff-list">
            {items.map((a) => (
              <li key={a.id}>
                <span>{a.name}</span>
                <span className="ff-muted">{a.type}</span>
                {a.archived && <span className="ff-muted">(archived)</span>}
              </li>
            ))}
          </ul>
        )}
      </DataView>
    </section>
  );
}
