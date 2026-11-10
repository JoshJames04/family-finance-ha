/** Upcoming: the forward timeline of expected movements, plus the
 * recurring bills (commitments) and transfer rules that generate it. */
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { ArrowLeftRight, CalendarClock, Plus, Receipt } from "lucide-react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { useAccounts, useCategories, useReady, useUpcoming } from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Panel } from "../components/Panel";
import { Money } from "../components/Money";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Dialog } from "../components/Dialog";
import { Field, Input, Select } from "../components/Field";
import { IconTile, PageHeader, Segmented, Stat } from "../components/Display";
import { Skeleton } from "../components/Feedback";
import {
  daysUntil, formatMonthOf, formatPence, formatRelativeDays,
  formatSmartDate, formatWeekdayDayMonth, toISODate,
} from "../lib/format";

type Movement = components["schemas"]["ExpectedMovementOut"];
type Frequency = components["schemas"]["Frequency"];

const FREQ: Frequency[] = ["weekly", "fortnightly", "monthly", "quarterly", "annually"];
const SOURCE_LABEL: Record<string, string> = {
  commitment: "Bill", payroll_run: "Payday", transfer_rule: "Transfer",
  savings_goal: "Goal", subscription: "Renewal", manual: "Scheduled",
};

type Tab = "timeline" | "bills" | "transfers";

export function Upcoming() {
  const ready = useReady();
  const [tab, setTab] = useState<Tab>("timeline");
  const [rev, setRev] = useState(0);
  const [adding, setAdding] = useState(false);
  const state = useUpcoming(90, rev);
  const commitments = useApi(() => api.get("/api/v1/commitments"), ready, [rev]);
  const transfers = useApi(() => api.get("/api/v1/transfer-rules"), ready, [rev]);
  const accounts = useAccounts();
  const accountName = (id: number | null | undefined) =>
    accounts.data?.items.find((a) => a.id === id)?.name ?? "—";

  const next30 = useMemo(() => {
    const items = (state.data ?? []).filter((m) => daysUntil(m.expected_date) <= 30);
    const inP = items.filter((m) => m.amount_pence > 0).reduce((s, m) => s + m.amount_pence, 0);
    const outP = items.filter((m) => m.amount_pence < 0).reduce((s, m) => s + m.amount_pence, 0);
    return { inP, outP, count: items.length };
  }, [state.data]);

  const groups = useMemo(() => {
    const m = new Map<string, Movement[]>();
    for (const mv of state.data ?? []) {
      const k = mv.expected_date.slice(0, 7);
      m.set(k, [...(m.get(k) ?? []), mv]);
    }
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [state.data]);

  return (
    <div>
      <PageHeader
        title="Upcoming"
        description="Bills, transfers and paydays over the next 90 days."
        actions={<Button variant="primary" onClick={() => setAdding(true)}><Plus /> Add bill</Button>}
      />

      <div className="mb-4 grid grid-cols-3 gap-3">
        {state.loading ? [0, 1, 2].map((i) => <Skeleton key={i} className="h-24 rounded-xl" />) : (
          <>
            <Stat className="rounded-xl border border-border bg-surface p-4 shadow-card" label="Coming in · 30 days" value={<Money pence={next30.inP} className="text-positive" />} />
            <Stat className="rounded-xl border border-border bg-surface p-4 shadow-card" label="Going out · 30 days" value={<Money pence={Math.abs(next30.outP)} />} />
            <Stat className="rounded-xl border border-border bg-surface p-4 shadow-card" label="Net · 30 days" value={<Money pence={next30.inP + next30.outP} signed />} hint={`${next30.count} movement${next30.count === 1 ? "" : "s"}`} />
          </>
        )}
      </div>

      <Segmented
        label="View"
        value={tab}
        onChange={setTab}
        className="mb-4"
        options={[
          { value: "timeline", label: "Timeline" },
          { value: "bills", label: `Bills${commitments.data ? ` (${commitments.data.total})` : ""}` },
          { value: "transfers", label: `Transfers${transfers.data ? ` (${transfers.data.total})` : ""}` },
        ]}
      />

      {tab === "timeline" && (
        <DataView state={state} empty="Nothing scheduled" emptyIcon={CalendarClock}
          emptyDescription="Add recurring bills, set up payroll or transfer rules and they'll appear here automatically."
          emptyAction={<Button variant="primary" onClick={() => setAdding(true)}><Plus /> Add a bill</Button>}>
          {() => (
            <div className="flex flex-col gap-4">
              {groups.map(([month, items]) => (
                <Panel key={month} title={formatMonthOf(`${month}-01`)} flush
                  actions={<Money pence={items.reduce((s, m) => s + m.amount_pence, 0)} signed tone="auto" className="text-[13px] font-medium" />}>
                  <div className="overflow-x-auto">
                  <table className="ff-table w-full text-[13.5px]">
                    <tbody>
                      {items.map((m) => (
                        <tr key={m.id} className="border-b border-border last:border-0 hover:bg-surface-2/60">
                          <td className="w-px py-2.5 pl-4"><IconTile label={m.label} size="sm" /></td>
                          <td className="px-3 py-2.5">
                            <p className="font-medium">{m.label}</p>
                            <p className="text-[12px] text-fg-subtle">{formatWeekdayDayMonth(m.expected_date)} · {formatRelativeDays(m.expected_date)}</p>
                          </td>
                          <td className="hidden px-3 sm:table-cell">
                            <div className="flex gap-1.5">
                              <Badge tone="neutral" dot={false}>{SOURCE_LABEL[m.source_type] ?? m.source_type}</Badge>
                              {m.confidence === "estimated" && <Badge tone="warning">Estimated</Badge>}
                            </div>
                          </td>
                          <td className="hidden px-3 text-[12.5px] text-fg-subtle md:table-cell">{accountName(m.account_id)}</td>
                          <td className="px-3 pr-4 text-right"><Money pence={m.amount_pence} signed tone="auto" className="font-medium" /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  </div>
                </Panel>
              ))}
            </div>
          )}
        </DataView>
      )}

      {tab === "bills" && (
        <Panel flush>
          <DataView state={{ ...commitments, data: commitments.data?.items ?? null }} empty="No recurring bills yet" emptyIcon={Receipt}
            emptyAction={<Button variant="primary" onClick={() => setAdding(true)}><Plus /> Add a bill</Button>}>
            {(items) => (
              <div className="overflow-x-auto">
              <table className="ff-table w-full text-[13.5px]">
                <thead className="text-left text-[12px] text-fg-subtle">
                  <tr className="h-9 border-b border-border">
                    <th className="pl-4 font-medium">Bill</th>
                    <th className="hidden px-3 font-medium sm:table-cell">Frequency</th>
                    <th className="px-3 font-medium">Next</th>
                    <th className="px-3 pr-4 text-right font-medium">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((c) => (
                    <tr key={c.id} className="border-b border-border last:border-0">
                      <td className="py-2.5 pl-4">
                        <div className="flex items-center gap-2.5">
                          <IconTile label={c.name} size="sm" />
                          <div>
                            <p className="font-medium">{c.name}</p>
                            <p className="text-[12px] text-fg-subtle">{accountName(c.account_id)}{c.active ? "" : " · inactive"}</p>
                          </div>
                        </div>
                      </td>
                      <td className="hidden px-3 capitalize text-fg-muted sm:table-cell">{c.frequency}</td>
                      <td className="px-3 text-fg-muted">{formatSmartDate(c.next_date)}</td>
                      <td className="px-3 pr-4 text-right font-medium">{c.amount_pence != null ? <Money pence={c.amount_pence} /> : <span className="text-fg-subtle">{c.formula ?? "Variable"}</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            )}
          </DataView>
        </Panel>
      )}

      {tab === "transfers" && (
        <Panel flush>
          <DataView state={{ ...transfers, data: transfers.data?.items ?? null }} empty="No transfer rules" emptyIcon={ArrowLeftRight}
            emptyDescription="Regular moves between accounts — e.g. savings on payday.">
            {(items) => (
              <ul className="divide-y divide-border">
                {items.map((t) => (
                  <li key={t.id} className="flex items-center gap-3 px-4 py-3">
                    <IconTile icon={ArrowLeftRight} label="transfer" size="sm" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium">{t.name}</p>
                      <p className="truncate text-[12px] text-fg-subtle">{accountName(t.source_account_id)} → {accountName(t.dest_account_id)} · <span className="capitalize">{t.frequency}</span> on day {t.day_of_month}</p>
                    </div>
                    <Badge tone={t.active ? "success" : "neutral"}>{t.active ? "Active" : "Paused"}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </DataView>
        </Panel>
      )}

      <AddBillDialog open={adding} onOpenChange={setAdding} onSaved={() => { setRev((r) => r + 1); setTab("timeline"); }} />
    </div>
  );
}

function AddBillDialog({ open, onOpenChange, onSaved }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const accounts = useAccounts();
  const categories = useCategories();
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [frequency, setFrequency] = useState<Frequency>("monthly");
  const [nextDate, setNextDate] = useState(toISODate(new Date()));
  const [accountId, setAccountId] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [busy, setBusy] = useState(false);
  const acct = accountId || String(accounts.data?.items[0]?.id ?? "");
  const valid = name.trim() && acct && /^\d+(\.\d{1,2})?$/.test(amount);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    api.post("/api/v1/commitments", {
      body: {
        name: name.trim(),
        account_id: Number(acct),
        category_id: categoryId ? Number(categoryId) : null,
        amount: `-${amount}`,
        frequency,
        next_date: nextDate,
        day_of_month: frequency === "monthly" ? Number(nextDate.slice(8)) : null,
      },
    })
      .then((c) => api.post("/api/v1/commitments/{cid}/materialise", { path: { cid: c.id } }).catch(() => undefined))
      .then(() => {
        toast.success(`Added ${name.trim()}`);
        setName(""); setAmount("");
        onSaved();
        onOpenChange(false);
      })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Add recurring bill"
      description="Bills generate expected movements on the timeline and in your forecast."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-add-bill" disabled={!valid || busy}>{busy ? "Saving…" : "Add bill"}</Button>
      </>}>
      <form id="ff-add-bill" onSubmit={submit} className="grid gap-4">
        <Field label="Name">{(id) => <Input id={id} autoFocus placeholder="e.g. Council Tax" value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Amount (£)">{(id) => <Input id={id} inputMode="decimal" placeholder="0.00" value={amount} onChange={(e) => setAmount(e.target.value.trim())} />}</Field>
          <Field label="Frequency">{(id) => (
            <Select id={id} value={frequency} onChange={(e) => setFrequency(e.target.value as Frequency)}>
              {FREQ.map((f) => <option key={f} value={f}>{f[0]!.toUpperCase() + f.slice(1)}</option>)}
            </Select>
          )}</Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Next due">{(id) => <Input id={id} type="date" value={nextDate} onChange={(e) => setNextDate(e.target.value)} />}</Field>
          <Field label="Paid from">{(id) => (
            <Select id={id} value={acct} onChange={(e) => setAccountId(e.target.value)}>
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
        <p className="text-[12.5px] text-fg-subtle">Amount recorded as an outgoing of {amount ? formatPence(-Math.round(Number(amount) * 100)) : "£0.00"}.</p>
      </form>
    </Dialog>
  );
}
