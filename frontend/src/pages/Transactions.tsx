import { api, formatPence } from "../api/client";
import { useApi, useHouseholds } from "../api/useApi";
import { DataView } from "../components/DataView";

export function Transactions() {
  const households = useHouseholds();
  const state = useApi(
    () => api.get("/api/v1/transactions"),
    households.data !== null,
  );
  return (
    <section>
      <h1>Transactions</h1>
      <DataView
        state={{ ...state, data: state.data?.items ?? null }}
        empty="No transactions recorded."
      >
        {(items) => (
          <table className="ff-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Payee</th>
                <th>Status</th>
                <th className="ff-num">Amount</th>
              </tr>
            </thead>
            <tbody>
              {items.map((t) => (
                <tr key={t.id}>
                  <td>{t.date}</td>
                  <td>{t.payee}</td>
                  <td className="ff-muted">{t.status}</td>
                  <td className={`ff-num ${t.amount_pence < 0 ? "ff-neg" : "ff-pos"}`}>
                    {formatPence(t.amount_pence)}
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
