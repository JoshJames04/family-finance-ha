/** Gift planner (issue #97): per-recipient lifecycle idea → planned →
 * purchased → wrapped → given, planned-vs-actual variance per gift and
 * per recipient, event totals vs budget, pay-with-transaction-link. */
import { useMemo, useState } from "react";
import { api } from "../api/client";
import { useApi, useHouseholds } from "../api/useApi";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Badge } from "../components/Badge";
import { Money } from "../components/Money";
import { formatPence } from "../lib/format";

type Event = components["schemas"]["SeasonalEventOut"];
type Gift = components["schemas"]["GiftOut"];
type PurchaseStatus = components["schemas"]["PurchaseStatus"];

const NEXT: Partial<Record<PurchaseStatus, PurchaseStatus>> = {
  idea: "planned",
  planned: "purchased",
  purchased: "wrapped",
  wrapped: "given",
};

function variance(g: Gift): number | null {
  return g.actual_pence === null ? null : g.actual_pence - g.planned_pence;
}

export function Gifts() {
  const households = useHouseholds();
  const events = useApi(
    () => api.get("/api/v1/seasonal-events"), households.data !== null);
  const [eid, setEid] = useState<number | null>(null);
  const event = eid ?? events.data?.[0]?.id ?? null;
  const [rev, setRev] = useState(0);
  const reload = () => setRev((r) => r + 1);
  const gifts = useApi(
    () => api.get("/api/v1/seasonal-events/{eid}/gifts",
      { path: { eid: event! } }),
    event !== null,
    [event, rev],
  );
  const totals = useApi(
    () => api.get("/api/v1/seasonal-events/{eid}/totals",
      { path: { eid: event! } }),
    event !== null,
    [event, rev],
  );
  const byRecipient = useMemo(() => {
    const m = new Map<string, Gift[]>();
    for (const g of gifts.data ?? []) {
      m.set(g.recipient, [...(m.get(g.recipient) ?? []), g]);
    }
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [gifts.data]);

  const act = async (fn: () => Promise<unknown>) => {
    await fn();
    reload();
  };
  const transition = (g: Gift, to: PurchaseStatus) => act(() =>
    api.post("/api/v1/seasonal-events/{eid}/gifts/{gid}/transition",
      { path: { eid: g.event_id, gid: g.id }, body: { to } }));
  const pay = (g: Gift) => act(() =>
    api.post("/api/v1/seasonal-events/{eid}/gifts/{gid}/pay",
      { path: { eid: g.event_id, gid: g.id },
        body: { actual: { pence: g.actual_pence ?? g.planned_pence } } }));

  return (
    <section>
      <h1>Gifts</h1>
      <label>
        Event{" "}
        <select
          value={event ?? ""}
          onChange={(e) => setEid(Number(e.target.value))}
        >
          {(events.data ?? []).map((ev: Event) => (
            <option key={ev.id} value={ev.id}>{ev.name}</option>
          ))}
        </select>
      </label>
      <DataView state={totals} empty="No event selected.">
        {(t) => (
          <p className="ff-muted">
            Planned <Money pence={t.planned_pence} /> · Actual{" "}
            <Money pence={t.actual_pence} /> · Committed{" "}
            <Money pence={t.committed_pence} /> · Remaining{" "}
            <Money pence={t.remaining_pence} />
          </p>
        )}
      </DataView>
      <DataView state={gifts} empty="No gifts planned yet.">
        {() => (
          <>
            {byRecipient.map(([recipient, list]) => {
              const planned = list.reduce((s, g) => s + g.planned_pence, 0);
              const actuals = list.filter((g) => g.actual_pence !== null);
              const actual = actuals.reduce(
                (s, g) => s + (g.actual_pence ?? 0), 0);
              const v = actuals.length ? actual - planned : null;
              return (
                <section key={recipient} aria-label={recipient}>
                  <h2>
                    {recipient}{" "}
                    <span className="ff-muted">
                      {v === null
                        ? formatPence(planned)
                        : `${formatPence(actual)} vs ${formatPence(planned)} (${v >= 0 ? "+" : ""}${formatPence(v)})`}
                    </span>
                  </h2>
                  <ul className="ff-list">
                    {list.map((g) => {
                      const gv = variance(g);
                      const next = NEXT[g.purchase_status];
                      return (
                        <li key={g.id}>
                          <span>
                            {g.idea}{" "}
                            <Badge tone={
                              g.purchase_status === "given" ? "success"
                                : g.purchase_status === "idea"
                                  || g.purchase_status === "planned"
                                ? "neutral" : "info"
                            }>{g.purchase_status}</Badge>{" "}
                            <Badge tone={
                              g.payment_status === "paid" ? "success"
                                : g.payment_status === "refunded"
                                ? "warning" : "neutral"
                            }>{g.payment_status}</Badge>
                          </span>
                          <span className="ff-num">
                            <Money pence={g.planned_pence} />
                            {gv !== null && (
                              <span className={`ff-muted`}>
                                {" "}actual <Money pence={g.actual_pence!} /> (
                                {gv >= 0 ? "+" : ""}{formatPence(gv)})
                              </span>
                            )}
                          </span>
                          <span>
                            {next && (
                              <button onClick={() => transition(g, next)}>
                                Mark {next}
                              </button>
                            )}
                            {g.payment_status === "unpaid" && (
                              <button onClick={() => pay(g)}>Mark paid</button>
                            )}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              );
            })}
          </>
        )}
      </DataView>
    </section>
  );
}
