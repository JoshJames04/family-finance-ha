/** Gift planner (issue #97): per-recipient lifecycle idea → planned →
 * purchased → wrapped → given, planned-vs-actual variance per gift and
 * per recipient, event totals vs budget, pay-with-transaction-link and
 * year-on-year comparison (issue #99). */
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Check, Gift as GiftIcon, Plus } from "lucide-react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { useReady } from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Panel } from "../components/Panel";
import { Badge, type BadgeTone } from "../components/Badge";
import { Money } from "../components/Money";
import { Button } from "../components/Button";
import { Dialog } from "../components/Dialog";
import { Field, Input, Select } from "../components/Field";
import { IconTile, PageHeader, Progress, Stat } from "../components/Display";
import { formatPence } from "../lib/format";

type Event = components["schemas"]["SeasonalEventOut"];
type Gift = components["schemas"]["GiftOut"];
type PurchaseStatus = components["schemas"]["PurchaseStatus"];
type Comparison = components["schemas"]["EventComparisonOut"];

const NEXT: Partial<Record<PurchaseStatus, PurchaseStatus>> = {
  idea: "planned",
  planned: "purchased",
  purchased: "wrapped",
  wrapped: "given",
};

const PURCHASE_TONE: Record<string, BadgeTone> = {
  idea: "neutral", planned: "info", purchased: "accent",
  wrapped: "warning", given: "success",
};

function variance(g: Gift): number | null {
  return g.actual_pence === null ? null : g.actual_pence - g.planned_pence;
}

const delta = (n: number) => `${n >= 0 ? "+" : ""}${formatPence(n)}`;

/** YoY table (issue #99): totals, per-recipient drift, avg/gift. */
function ComparisonTable({ c }: { c: Comparison }) {
  return (
    <section aria-label="Year-on-year comparison" className="px-4 py-3">
      <p className="mb-3 text-[13px] text-fg-muted">
        Earlier <Money pence={c.earlier.actual_pence} /> → later{" "}
        <Money pence={c.later.actual_pence} /> ({delta(c.actual_delta_pence)})
        {" · "}avg/gift{" "}
        {c.earlier_avg_gift_pence === null ? "—"
          : formatPence(c.earlier_avg_gift_pence)} →{" "}
        {c.later_avg_gift_pence === null ? "—"
          : formatPence(c.later_avg_gift_pence)}
      </p>
      <table className="ff-table w-full text-[13.5px]">
        <thead className="text-left text-[12px] text-fg-subtle">
          <tr className="h-8 border-b border-border">
            <th className="font-medium">Recipient</th>
            <th className="px-3 text-right font-medium">Earlier</th>
            <th className="px-3 text-right font-medium">Later</th>
            <th className="px-3 text-right font-medium">Δ</th>
          </tr>
        </thead>
        <tbody>
          {c.recipients.map((r) => (
            <tr key={r.recipient} className="border-b border-border last:border-0">
              <td className="py-2 font-medium">{r.recipient}</td>
              <td className="px-3 py-2 text-right"><Money pence={r.earlier_actual_pence} /></td>
              <td className="px-3 py-2 text-right"><Money pence={r.later_actual_pence} /></td>
              <td className="px-3 py-2 text-right">{delta(r.delta_pence)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function Gifts() {
  const ready = useReady();
  const events = useApi(() => api.get("/api/v1/seasonal-events"), ready);
  const [eid, setEid] = useState<number | null>(null);
  const event = eid ?? events.data?.[0]?.id ?? null;
  const [rev, setRev] = useState(0);
  const reload = () => setRev((r) => r + 1);
  const [addingGift, setAddingGift] = useState(false);
  const [addingEvent, setAddingEvent] = useState(false);
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
  const [cmpId, setCmpId] = useState<number | null>(null);
  const eventObj = (events.data ?? []).find((e) => e.id === event);
  const cmpCandidates = (events.data ?? []).filter(
    (e) => eventObj && e.id !== event && e.type === eventObj.type);
  const cmp = useApi(
    () => api.get(
      "/api/v1/seasonal-events/{eid}/compare/{other_id}",
      { path: { eid: event!, other_id: cmpId! } }),
    event !== null && cmpId !== null,
    [event, cmpId],
  );
  const byRecipient = useMemo(() => {
    const m = new Map<string, Gift[]>();
    for (const g of gifts.data ?? []) {
      m.set(g.recipient, [...(m.get(g.recipient) ?? []), g]);
    }
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [gifts.data]);

  const act = async (fn: () => Promise<unknown>) => {
    await fn().catch((e: Error) => toast.error(e.message));
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
    <div>
      <PageHeader
        title="Gifts"
        description="Seasonal gift planning — ideas through to given, tracked against budget."
        actions={
          <>
            <Select
              aria-label="Event"
              value={event ?? ""}
              wrapperClassName="w-44"
              onChange={(e) => setEid(Number(e.target.value))}
            >
              {(events.data ?? []).map((ev: Event) => (
                <option key={ev.id} value={ev.id}>{ev.name}</option>
              ))}
            </Select>
            <Button variant="secondary" onClick={() => setAddingEvent(true)}><Plus /> New event</Button>
            <Button variant="primary" onClick={() => setAddingGift(true)} disabled={event === null}>
              <Plus /> Add gift
            </Button>
          </>
        }
      />

      {eventObj && totals.data && (
        <Panel className="mb-4">
          <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Planned" value={<Money pence={totals.data.planned_pence} />} />
            <Stat label="Actual" value={<Money pence={totals.data.actual_pence} />} />
            <Stat label="Committed" value={<Money pence={totals.data.committed_pence} />} />
            <Stat label="Remaining" value={<Money pence={totals.data.remaining_pence} className={totals.data.remaining_pence < 0 ? "text-negative" : ""} />} />
          </div>
          <div className="flex items-center gap-3">
            <Progress
              value={totals.data.actual_pence}
              max={eventObj.budget_pence}
              className="flex-1"
              label={`${eventObj.name} budget used`}
            />
            <span className="tnum text-[12.5px] whitespace-nowrap text-fg-subtle">
              <Money pence={totals.data.actual_pence} /> of <Money pence={eventObj.budget_pence} />
            </span>
          </div>
        </Panel>
      )}

      {cmpCandidates.length > 0 && (
        <div className="mb-4 flex items-center gap-2">
          <Select
            aria-label="Compare with"
            value={cmpId ?? ""}
            wrapperClassName="w-56"
            onChange={(e) =>
              setCmpId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Compare with another year…</option>
            {cmpCandidates.map((ev) => (
              <option key={ev.id} value={ev.id}>{ev.name}</option>
            ))}
          </Select>
        </div>
      )}
      {cmp.data && (
        <Panel flush className="mb-4" title="Year-on-year">
          <ComparisonTable c={cmp.data} />
        </Panel>
      )}

      <DataView
        state={gifts}
        empty={event === null ? "No events yet" : "No gifts planned yet"}
        emptyIcon={GiftIcon}
        emptyDescription={event === null
          ? "Create a seasonal event — Christmas, birthdays — to plan gifts against a budget."
          : "Add recipients and ideas to plan this event."}
        emptyAction={event === null
          ? <Button variant="primary" onClick={() => setAddingEvent(true)}><Plus /> New event</Button>
          : <Button variant="primary" onClick={() => setAddingGift(true)}><Plus /> Add gift</Button>}
      >
        {() => (
          <div className="grid gap-4 xl:grid-cols-2">
            {byRecipient.map(([recipient, list]) => {
              const planned = list.reduce((s, g) => s + g.planned_pence, 0);
              const actuals = list.filter((g) => g.actual_pence !== null);
              const actual = actuals.reduce(
                (s, g) => s + (g.actual_pence ?? 0), 0);
              const v = actuals.length ? actual - planned : null;
              return (
                <Panel
                  key={recipient}
                  aria-label={recipient}
                  title={recipient}
                  flush
                  actions={
                    <span className="tnum text-[12.5px] text-fg-subtle">
                      {v === null
                        ? formatPence(planned)
                        : `${formatPence(actual)} vs ${formatPence(planned)} (${v >= 0 ? "+" : ""}${formatPence(v)})`}
                    </span>
                  }
                >
                  <ul className="divide-y divide-border">
                    {list.map((g) => {
                      const gv = variance(g);
                      const next = NEXT[g.purchase_status];
                      return (
                        <li key={g.id} className="flex items-center gap-3 px-4 py-3">
                          <IconTile icon={GiftIcon} label={g.idea} size="sm" />
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-[13.5px] font-medium">{g.idea}</p>
                            <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
                              <Badge tone={PURCHASE_TONE[g.purchase_status] ?? "neutral"}>{g.purchase_status}</Badge>
                              <Badge tone={
                                g.payment_status === "paid" ? "success"
                                  : g.payment_status === "refunded" ? "warning" : "neutral"
                              }>{g.payment_status}</Badge>
                            </div>
                          </div>
                          <div className="text-right text-[13px]">
                            <Money pence={g.planned_pence} className="font-medium" />
                            {gv !== null && (
                              <p className="tnum text-[12px] text-fg-subtle">
                                actual <Money pence={g.actual_pence!} /> (
                                {gv >= 0 ? "+" : ""}{formatPence(gv)})
                              </p>
                            )}
                          </div>
                          <div className="flex shrink-0 gap-1">
                            {next && (
                              <Button size="sm" variant="secondary" onClick={() => transition(g, next)}>
                                Mark {next}
                              </Button>
                            )}
                            {g.payment_status === "unpaid" && (
                              <Button size="sm" variant="ghost" onClick={() => pay(g)}>
                                <Check /> Mark paid
                              </Button>
                            )}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </Panel>
              );
            })}
          </div>
        )}
      </DataView>

      <AddGiftDialog open={addingGift} onOpenChange={setAddingGift} eventId={event} onSaved={reload} />
      <NewEventDialog open={addingEvent} onOpenChange={setAddingEvent} onSaved={(id) => { reload(); setEid(id); }} />
    </div>
  );
}

function AddGiftDialog({ open, onOpenChange, eventId, onSaved }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  eventId: number | null;
  onSaved: () => void;
}) {
  const [recipient, setRecipient] = useState("");
  const [idea, setIdea] = useState("");
  const [planned, setPlanned] = useState("");
  const [busy, setBusy] = useState(false);
  const valid = recipient.trim() && idea.trim() && /^\d+(\.\d{1,2})?$/.test(planned);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid || eventId === null) return;
    setBusy(true);
    api.post("/api/v1/seasonal-events/{eid}/gifts", {
      path: { eid: eventId },
      body: { recipient: recipient.trim(), idea: idea.trim(), planned },
    })
      .then(() => {
        toast.success(`Added ${idea.trim()} for ${recipient.trim()}`);
        setRecipient(""); setIdea(""); setPlanned("");
        onSaved();
        onOpenChange(false);
      })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Add gift"
      description="An idea or planned purchase for this event."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-add-gift" disabled={!valid || busy}>
          {busy ? "Adding…" : "Add gift"}
        </Button>
      </>}>
      <form id="ff-add-gift" onSubmit={submit} className="grid gap-4">
        <Field label="Recipient">{(id) => <Input id={id} autoFocus placeholder="e.g. Sam" value={recipient} onChange={(e) => setRecipient(e.target.value)} />}</Field>
        <Field label="Gift idea">{(id) => <Input id={id} placeholder="e.g. Lego set" value={idea} onChange={(e) => setIdea(e.target.value)} />}</Field>
        <Field label="Planned spend (£)">{(id) => <Input id={id} inputMode="decimal" placeholder="0.00" value={planned} onChange={(e) => setPlanned(e.target.value.trim())} />}</Field>
      </form>
    </Dialog>
  );
}

function NewEventDialog({ open, onOpenChange, onSaved }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSaved: (id: number) => void;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState("christmas");
  const [year, setYear] = useState(String(new Date().getFullYear()));
  const [budget, setBudget] = useState("");
  const [busy, setBusy] = useState(false);
  const valid = name.trim() && /^\d{4}$/.test(year) && /^\d+(\.\d{1,2})?$/.test(budget);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    api.post("/api/v1/seasonal-events", {
      body: { name: name.trim(), type, year: Number(year), budget },
    })
      .then((ev) => {
        toast.success(`Created ${ev.name}`);
        onSaved(ev.id);
        onOpenChange(false);
      })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="New event"
      description="A seasonal occasion with its own gift budget."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-new-event" disabled={!valid || busy}>
          {busy ? "Creating…" : "Create event"}
        </Button>
      </>}>
      <form id="ff-new-event" onSubmit={submit} className="grid gap-4">
        <Field label="Name">{(id) => <Input id={id} autoFocus placeholder="e.g. Christmas 2026" value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Type">{(id) => (
            <Select id={id} value={type} onChange={(e) => setType(e.target.value)}>
              <option value="christmas">Christmas</option>
              <option value="birthday">Birthday</option>
              <option value="other">Other</option>
            </Select>
          )}</Field>
          <Field label="Year">{(id) => <Input id={id} inputMode="numeric" value={year} onChange={(e) => setYear(e.target.value.trim())} />}</Field>
        </div>
        <Field label="Budget (£)">{(id) => <Input id={id} inputMode="decimal" placeholder="0.00" value={budget} onChange={(e) => setBudget(e.target.value.trim())} />}</Field>
      </form>
    </Dialog>
  );
}
