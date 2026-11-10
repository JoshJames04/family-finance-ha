/** Subscriptions: recurring services with renewal tracking, the
 * annualised cost view, price-change history, auto-detected candidates
 * and cancel/record-payment flows. */
import { useState } from "react";
import { toast } from "sonner";
import { Plus, Radar, Repeat, TrendingUp } from "lucide-react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { useAccounts, useCategories, useReady } from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Panel } from "../components/Panel";
import { Badge, type BadgeTone } from "../components/Badge";
import { Money } from "../components/Money";
import { Button } from "../components/Button";
import { Dialog } from "../components/Dialog";
import { Field, Input, Select } from "../components/Field";
import { IconTile, PageHeader, Segmented, Stat } from "../components/Display";
import {
  daysUntil, formatDayMonth, formatRelativeDays, formatSmartDate,
  toISODate,
} from "../lib/format";

type Sub = components["schemas"]["SubscriptionOut"];
type Candidate = components["schemas"]["DetectionCandidateOut"];
type PriceChange = components["schemas"]["PriceChangeEventOut"];
type Frequency = components["schemas"]["Frequency"];

const FREQ: Frequency[] = ["weekly", "fortnightly", "monthly", "quarterly", "annually"];
const FREQ_MONTHS: Record<Frequency, number> = {
  weekly: 52 / 12, fortnightly: 26 / 12, monthly: 1, quarterly: 1 / 3, annually: 1 / 12,
};

function monthlyPence(s: Sub): number {
  return Math.round(s.expected_pence * FREQ_MONTHS[s.frequency]);
}

export function Subscriptions() {
  const ready = useReady();
  const [rev, setRev] = useState(0);
  const [tab, setTab] = useState<"active" | "cancelled">("active");
  const [adding, setAdding] = useState(false);
  const [paying, setPaying] = useState<Sub | null>(null);
  const [cancelling, setCancelling] = useState<Sub | null>(null);
  const subs = useApi(
    () => api.get("/api/v1/subscriptions", { query: { include_cancelled: true } }),
    ready, [rev]);
  const annualised = useApi(() => api.get("/api/v1/subscriptions/annualised"), ready, [rev]);
  const priceChanges = useApi(() => api.get("/api/v1/subscriptions/price-changes"), ready, [rev]);
  const candidates = useApi(() => api.get("/api/v1/subscriptions/candidates"), ready, [rev]);
  const categories = useCategories();
  const refresh = () => setRev((r) => r + 1);

  const list = (subs.data ?? []).filter((s) =>
    tab === "cancelled" ? s.status === "cancelled" : s.status !== "cancelled");
  const pending = (Array.isArray(candidates.data) ? candidates.data : [])
    .filter((c) => c.status === "pending");
  const monthlyTotal = list
    .filter((s) => s.status !== "cancelled")
    .reduce((sum, s) => sum + monthlyPence(s), 0);
  const next = [...list]
    .filter((s) => s.status !== "cancelled")
    .sort((a, b) => a.next_renewal_date.localeCompare(b.next_renewal_date))[0];

  const runDetection = () =>
    api.post("/api/v1/subscriptions/detect", { body: { min_occurrences: 3 } })
      .then((found) => {
        toast.success(found.length
          ? `Found ${found.length} possible subscription${found.length === 1 ? "" : "s"}`
          : "No new patterns found");
        refresh();
      })
      .catch((e: Error) => toast.error(e.message));
  const candidateAct = (c: Candidate, action: "confirm" | "dismiss") =>
    api.post(
      action === "confirm"
        ? "/api/v1/subscriptions/candidates/{cid}/confirm"
        : "/api/v1/subscriptions/candidates/{cid}/dismiss",
      { path: { cid: c.id }, body: {} },
    ).then(() => {
      toast.success(action === "confirm" ? `Now tracking ${c.payee}` : "Dismissed");
      refresh();
    }).catch((e: Error) => toast.error(e.message));

  return (
    <div>
      <PageHeader
        title="Subscriptions"
        description="Recurring services, renewals and what they really cost per year."
        actions={
          <>
            <Button variant="secondary" onClick={runDetection}><Radar /> Scan transactions</Button>
            <Button variant="primary" onClick={() => setAdding(true)}><Plus /> Add subscription</Button>
          </>
        }
      />

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat className="rounded-xl border border-border bg-surface p-4 shadow-card" label="Monthly equivalent"
          value={<Money pence={monthlyTotal} />} hint="across active services" />
        <Stat className="rounded-xl border border-border bg-surface p-4 shadow-card" label="Annualised"
          value={annualised.data ? <Money pence={annualised.data.total_annualised_pence} /> : "—"}
          hint="true yearly cost" />
        <Stat className="rounded-xl border border-border bg-surface p-4 shadow-card" label="Active"
          value={String((subs.data ?? []).filter((s) => s.status !== "cancelled").length)}
          hint={pending.length ? `${pending.length} candidate${pending.length === 1 ? "" : "s"} to review` : undefined} />
        <Stat className="rounded-xl border border-border bg-surface p-4 shadow-card" label="Next renewal"
          value={next ? formatDayMonth(next.next_renewal_date) : "—"}
          hint={next ? `${next.payee} · ${formatRelativeDays(next.next_renewal_date)}` : undefined} />
      </div>

      {pending.length > 0 && (
        <Panel flush className="mb-4" title={`Possible subscriptions (${pending.length})`}
          description="Recurring payments spotted in your transactions.">
          <ul className="divide-y divide-border">
            {pending.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
                <IconTile icon={Repeat} label={c.payee} size="sm" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13.5px] font-medium">{c.payee}</p>
                  <p className="text-[12px] text-fg-subtle">
                    <span className="capitalize">{c.frequency}</span> · seen {c.occurrence_count}× · {c.confidence}% confidence
                  </p>
                </div>
                <Money pence={c.expected_pence} className="font-medium" />
                <div className="flex gap-1.5">
                  <Button size="sm" variant="primary" onClick={() => candidateAct(c, "confirm")}>Track it</Button>
                  <Button size="sm" variant="ghost" onClick={() => candidateAct(c, "dismiss")}>Dismiss</Button>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <Segmented label="Status" value={tab} onChange={setTab} className="mb-3"
        options={[{ value: "active", label: "Active" }, { value: "cancelled", label: "Cancelled" }]} />

      <Panel flush>
        <DataView
          state={{ ...subs, data: list }}
          empty={tab === "cancelled" ? "Nothing cancelled" : "No subscriptions tracked"}
          emptyIcon={Repeat}
          emptyDescription="Add your recurring services manually or scan transactions to detect them."
          emptyAction={<Button variant="primary" onClick={() => setAdding(true)}><Plus /> Add subscription</Button>}
        >
          {(items) => (
            <div className="overflow-x-auto">
            <table className="ff-table w-full text-[13.5px]">
              <thead className="text-left text-[12px] text-fg-subtle">
                <tr className="h-9 border-b border-border">
                  <th className="pl-4 font-medium">Service</th>
                  <th className="hidden px-3 font-medium sm:table-cell">Frequency</th>
                  <th className="px-3 font-medium">Next renewal</th>
                  <th className="hidden px-3 text-right font-medium md:table-cell">Per month</th>
                  <th className="px-3 pr-4 text-right font-medium">Amount</th>
                  <th className="w-px pr-3"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {items.map((s) => {
                  const overdue = s.overdue === true || (s.status !== "cancelled" && daysUntil(s.next_renewal_date) < 0);
                  return (
                    <tr key={s.id} className="border-b border-border last:border-0 hover:bg-surface-2/60">
                      <td className="py-2.5 pl-4">
                        <div className="flex items-center gap-2.5">
                          <IconTile label={s.payee} size="sm" />
                          <div>
                            <p className="font-medium">{s.payee}</p>
                            <p className="text-[12px] text-fg-subtle">
                              {s.category_id ? categories.byId.get(s.category_id)?.name ?? "" : ""}
                              {s.status === "cancelled" && s.cancelled_date ? `Cancelled ${formatSmartDate(s.cancelled_date)}` : ""}
                            </p>
                          </div>
                        </div>
                      </td>
                      <td className="hidden px-3 capitalize text-fg-muted sm:table-cell">{s.frequency}</td>
                      <td className="px-3">
                        <span className="text-fg-muted">{formatSmartDate(s.next_renewal_date)}</span>
                        {overdue && <Badge tone="warning" className="ml-2">overdue</Badge>}
                      </td>
                      <td className="tnum hidden px-3 text-right text-fg-muted md:table-cell"><Money pence={monthlyPence(s)} /></td>
                      <td className="px-3 pr-4 text-right font-medium"><Money pence={s.expected_pence} /></td>
                      <td className="pr-3">
                        {s.status !== "cancelled" && (
                          <div className="flex justify-end gap-1">
                            <Button size="sm" variant="ghost" onClick={() => setPaying(s)}>Record payment</Button>
                            <Button size="sm" variant="ghost" onClick={() => setCancelling(s)}>Cancel</Button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
          )}
        </DataView>
      </Panel>

      {(priceChanges.data ?? []).length > 0 && (
        <Panel flush className="mt-4" title="Price changes" description="Detected changes in what your services charge.">
          <ul className="divide-y divide-border">
            {(priceChanges.data ?? []).slice(0, 8).map((p: PriceChange) => {
              const name = subs.data?.find((s) => s.id === p.subscription_id)?.payee ?? `Subscription #${p.subscription_id}`;
              return (
                <li key={p.id} className="flex items-center gap-3 px-4 py-2.5 text-[13px]">
                  <TrendingUp className={p.delta_pence > 0 ? "size-4 text-negative" : "size-4 text-positive"} />
                  <span className="flex-1 font-medium">{name}</span>
                  <span className="tnum text-fg-muted">
                    <Money pence={p.old_pence} /> → <Money pence={p.new_pence} />
                  </span>
                  <Money pence={p.delta_pence} signed tone="variance" />
                  <span className="text-[12px] text-fg-subtle">{formatDayMonth(p.detected_date)}</span>
                </li>
              );
            })}
          </ul>
        </Panel>
      )}

      <AddSubDialog open={adding} onOpenChange={setAdding} onSaved={refresh} />
      {paying && <PaymentDialog sub={paying} onOpenChange={(o) => { if (!o) setPaying(null); }} onSaved={refresh} />}
      {cancelling && <CancelDialog sub={cancelling} onOpenChange={(o) => { if (!o) setCancelling(null); }} onSaved={refresh} />}
    </div>
  );
}

function AddSubDialog({ open, onOpenChange, onSaved }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const categories = useCategories();
  const accounts = useAccounts();
  const [payee, setPayee] = useState("");
  const [amount, setAmount] = useState("");
  const [frequency, setFrequency] = useState<Frequency>("monthly");
  const [renewal, setRenewal] = useState(toISODate(new Date()));
  const [categoryId, setCategoryId] = useState("");
  const [accountId, setAccountId] = useState("");
  const [busy, setBusy] = useState(false);
  const valid = payee.trim() && /^\d+(\.\d{1,2})?$/.test(amount);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    api.post("/api/v1/subscriptions", {
      body: {
        payee: payee.trim(),
        frequency,
        expected: amount,
        next_renewal_date: renewal,
        category_id: categoryId ? Number(categoryId) : null,
        account_id: accountId ? Number(accountId) : null,
        tolerance_pct: 10,
      },
    })
      .then(() => {
        toast.success(`Now tracking ${payee.trim()}`);
        setPayee(""); setAmount("");
        onSaved();
        onOpenChange(false);
      })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Add subscription"
      description="Track a recurring service — renewals appear in Upcoming and the forecast."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-add-sub" disabled={!valid || busy}>{busy ? "Adding…" : "Add subscription"}</Button>
      </>}>
      <form id="ff-add-sub" onSubmit={submit} className="grid gap-4">
        <Field label="Service">{(id) => <Input id={id} autoFocus placeholder="e.g. Spotify" value={payee} onChange={(e) => setPayee(e.target.value)} />}</Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Amount (£)">{(id) => <Input id={id} inputMode="decimal" placeholder="0.00" value={amount} onChange={(e) => setAmount(e.target.value.trim())} />}</Field>
          <Field label="Frequency">{(id) => (
            <Select id={id} value={frequency} onChange={(e) => setFrequency(e.target.value as Frequency)}>
              {FREQ.map((f) => <option key={f} value={f}>{f[0]!.toUpperCase() + f.slice(1)}</option>)}
            </Select>
          )}</Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Next renewal">{(id) => <Input id={id} type="date" value={renewal} onChange={(e) => setRenewal(e.target.value)} />}</Field>
          <Field label="Paid from">{(id) => (
            <Select id={id} value={accountId} onChange={(e) => setAccountId(e.target.value)}>
              <option value="">Any account</option>
              {(accounts.data?.items ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select>
          )}</Field>
        </div>
        <Field label="Category">{(id) => (
          <Select id={id} value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
            <option value="">Uncategorised</option>
            {(categories.data?.items ?? []).filter((c) => !c.archived).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
        )}</Field>
      </form>
    </Dialog>
  );
}

function PaymentDialog({ sub, onOpenChange, onSaved }: {
  sub: Sub;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const [date, setDate] = useState(toISODate(new Date()));
  const [amount, setAmount] = useState((sub.expected_pence / 100).toString());
  const [busy, setBusy] = useState(false);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    api.post("/api/v1/subscriptions/{sid}/payments", {
      path: { sid: sub.id },
      body: { date, amount },
    })
      .then(() => { toast.success("Payment recorded"); onSaved(); onOpenChange(false); })
      .catch((err: Error) => { toast.error(err.message); setBusy(false); });
  };
  return (
    <Dialog open onOpenChange={onOpenChange} title={`Record payment — ${sub.payee}`}
      description="Log the actual charge; the next renewal date rolls forward."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Back</Button>
        <Button variant="primary" type="submit" form="ff-sub-pay" disabled={busy}>{busy ? "Saving…" : "Record payment"}</Button>
      </>}>
      <form id="ff-sub-pay" onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        <Field label="Amount (£)">{(id) => <Input id={id} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.trim())} />}</Field>
        <Field label="Date">{(id) => <Input id={id} type="date" value={date} onChange={(e) => setDate(e.target.value)} />}</Field>
      </form>
    </Dialog>
  );
}

function CancelDialog({ sub, onOpenChange, onSaved }: {
  sub: Sub;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const [date, setDate] = useState(toISODate(new Date()));
  const [busy, setBusy] = useState(false);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    api.post("/api/v1/subscriptions/{sid}/cancel", {
      path: { sid: sub.id },
      body: { date },
    })
      .then(() => { toast.success(`${sub.payee} cancelled`); onSaved(); onOpenChange(false); })
      .catch((err: Error) => { toast.error(err.message); setBusy(false); });
  };
  return (
    <Dialog open onOpenChange={onOpenChange} title={`Cancel ${sub.payee}?`}
      description="It stays in your history but drops out of renewals and the forecast."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Keep it</Button>
        <Button variant="danger" type="submit" form="ff-sub-cancel" disabled={busy}>{busy ? "Cancelling…" : "Cancel subscription"}</Button>
      </>}>
      <form id="ff-sub-cancel" onSubmit={submit} className="grid gap-4">
        <Field label="Cancelled on">{(id) => <Input id={id} type="date" value={date} onChange={(e) => setDate(e.target.value)} />}</Field>
      </form>
    </Dialog>
  );
}
