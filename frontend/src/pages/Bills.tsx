/** Bills: first-class payable objects (issue #274). Each commitment —
 * "Broadband, £28 on the 14th, from Joint" — is a managed thing with a
 * lifecycle: create, edit, pause, resume, post the next payment. Grouped
 * by the same category sections as the Budget view so the household's
 * money map stays consistent. */
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { undoToast } from "../lib/toast";
import {
  CalendarClock, Pause, Pencil, Play, Plus, ReceiptText, Send, Trash2,
} from "lucide-react";
import { api } from "../api/client";
import { useApi, useIdentity, useMeProfileId } from "../api/useApi";
import { useAccounts, useCategories, useReady } from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Dialog } from "../components/Dialog";
import { Field, Input, Select } from "../components/Field";
import { IconTile, PageHeader, Segmented, Stat } from "../components/Display";
import { EmptyState, Skeleton } from "../components/Feedback";
import { Money } from "../components/Money";
import { cn } from "../lib/cn";
import { formatDayMonth, formatWeekday, formatWeekdayDayMonth, parseISODate } from "../lib/format";

type Commitment = components["schemas"]["CommitmentOut"];
type Category = components["schemas"]["CategoryOut"];
type Frequency = components["schemas"]["Frequency"];

const FREQ_LABEL: Record<Frequency, string> = {
  weekly: "Weekly", fortnightly: "Fortnightly", monthly: "Monthly",
  quarterly: "Quarterly", annually: "Annually",
};

/** Normalised monthly cost for the summary strip (pence). */
function monthlyPence(amount: number, freq: Frequency): number {
  switch (freq) {
    case "weekly": return Math.round(amount * 52 / 12);
    case "fortnightly": return Math.round(amount * 26 / 12);
    case "monthly": return amount;
    case "quarterly": return Math.round(amount / 3);
    case "annually": return Math.round(amount / 12);
  }
}

function cadence(c: Commitment): string {
  const f = FREQ_LABEL[c.frequency];
  if (c.frequency === "weekly" || c.frequency === "fortnightly") {
    return `${f} · ${formatWeekday(c.next_date)}s`;
  }
  const dom = c.day_of_month ?? parseISODate(c.next_date).getDate();
  return `${f} · day ${dom}`;
}

/** Section a commitment belongs to — its category's parent, matching the
 * Budget view's grouping. */
function sectionOf(c: Commitment, byId: Map<number, Category>): string {
  const cat = c.category_id == null ? undefined : byId.get(c.category_id);
  const parent = cat?.parent_id != null ? byId.get(cat.parent_id) : undefined;
  return parent?.name ?? "";
}

export function Bills() {
  const ready = useReady();
  const [rev, setRev] = useState(0);
  const [scope, setScope] = useState<"all" | "mine" | "shared">("all");
  const commitments = useApi(
    () => api.get("/api/v1/commitments", { query: { include_inactive: true } }),
    ready, [rev]);
  const accounts = useAccounts(rev);
  const categories = useCategories();
  const identity = useIdentity();
  const me = useMeProfileId();
  const memberCount = (identity.data ?? []).filter((p) => p.is_active).length;
  const [editing, setEditing] = useState<Commitment | "new" | null>(null);
  const refresh = () => setRev((r) => r + 1);

  const accountsById = useMemo(() => {
    const m = new Map<number, components["schemas"]["AccountOut"]>();
    for (const a of accounts.data?.items ?? []) m.set(a.id, a);
    return m;
  }, [accounts.data]);
  const ownerOf = (c: Commitment) =>
    c.account_id == null ? null : accountsById.get(c.account_id)?.owner_profile_id ?? null;
  const inScope = (c: Commitment) =>
    scope === "all" || (scope === "mine" ? ownerOf(c) === me : ownerOf(c) === null);

  const items = commitments.data?.items ?? [];
  const active = items.filter((c) => c.active && inScope(c));
  const paused = items.filter((c) => !c.active && inScope(c));

  /* ---- summary ---- */
  const monthlyTotal = active.reduce(
    (s, c) => s + (c.amount_pence != null ? monthlyPence(Math.abs(c.amount_pence), c.frequency) : 0), 0);
  /* Next due = earliest unmaterialised occurrence dated today or
   * later (#412). next_date only advances on posting, so a past
   * value means the scheduled date arrived unpaid — that's overdue,
   * not upcoming, and is surfaced on its own tile. */
  const today = new Date().toISOString().slice(0, 10);
  const upcoming = active.filter((c) => c.next_date >= today)
    .sort((a, b) => a.next_date.localeCompare(b.next_date));
  const overdue = active.filter((c) => c.next_date < today)
    .sort((a, b) => a.next_date.localeCompare(b.next_date));
  const next = upcoming[0];
  const oldestOverdue = overdue[0];

  /* ---- grouping: same sections as Budget, ungrouped last ---- */
  const sections = useMemo(() => {
    const map = new Map<string, Commitment[]>();
    for (const c of [...active].sort((a, b) => a.next_date.localeCompare(b.next_date))) {
      const key = sectionOf(c, categories.byId);
      const list = map.get(key) ?? [];
      list.push(c);
      map.set(key, list);
    }
    return [...map.entries()].sort((a, b) =>
      a[0] === "" ? 1 : b[0] === "" ? -1 : a[0].localeCompare(b[0]));
  }, [active, categories.byId]);
  const sectioned = sections.some(([k]) => k !== "");

  const pause = (c: Commitment) =>
    api.delete("/api/v1/commitments/{cid}", { path: { cid: c.id } })
      .then(() => {
        undoToast(`${c.name} paused`, () =>
          api.post("/api/v1/commitments/{cid}/activate",
            { path: { cid: c.id } }).then(refresh));
        refresh();
      })
      .catch((e: Error) => toast.error(e.message));

  const remove = (c: Commitment) =>
    api.delete("/api/v1/commitments/{cid}",
      { path: { cid: c.id }, query: { permanent: true } })
      .then(() => {
        toast.success(`${c.name} removed`);
        refresh();
      })
      .catch((err: Error) => toast.error(err.message));
  const resume = (c: Commitment) =>
    api.post("/api/v1/commitments/{cid}/activate", { path: { cid: c.id } })
      .then(() => { toast.success(`${c.name} resumed`); refresh(); })
      .catch((e: Error) => toast.error(e.message));
  const postNext = (c: Commitment) =>
    api.post("/api/v1/commitments/{cid}/materialise", {
      path: { cid: c.id }, query: { horizon: c.next_date },
    }).then((r) => {
      toast.success(r.created.length
        ? `${c.name}: next payment posted` : `${c.name}: nothing new to post`);
      refresh();
    }).catch((e: Error) => toast.error(e.message));

  return (
    <div>
      <PageHeader
        title="Bills"
        description="Everything you pay on a schedule — each one an object, not a line item."
        actions={
          <div className="flex items-center gap-2">
            {memberCount > 1 && (
              <Segmented
                label="Bill scope"
                value={scope}
                onChange={setScope}
                options={[
                  { value: "all", label: "All" },
                  { value: "mine", label: "Mine" },
                  { value: "shared", label: "Shared" },
                ]}
              />
            )}
            <Button variant="primary" size="sm" onClick={() => setEditing("new")}>
              <Plus /> Add bill
            </Button>
          </div>
        }
      />

      <DataView
        state={{ ...commitments, data: commitments.data?.items ?? null }}
        empty="No bills set up"
        emptyDescription="Things you pay on a schedule — broadband, council tax, gym — live here and generate your upcoming movements."
        emptyIcon={ReceiptText}
        loading={<Skeleton className="h-64 w-full rounded-xl" />}
      >
        {() => (
          <>
            {/* #413: one column on narrow phones → no horizontal
                scroll; reflows to 2/3/5 columns as width allows. */}
            <div className="mb-4 grid grid-cols-1 gap-3 min-[380px]:grid-cols-2 md:grid-cols-3 xl:grid-cols-5">
              <Stat label="Monthly bills" value={<Money pence={monthlyTotal} />}
                hint={`${active.length} active`} className="rounded-xl border border-border bg-surface p-4 shadow-card" />
              <Stat label="Next due" value={next ? next.name : "—"}
                hint={next
                  ? next.next_date === today
                    ? "Due today"
                    : formatWeekdayDayMonth(next.next_date)
                  : undefined}
                className="rounded-xl border border-border bg-surface p-4 shadow-card" />
              <Stat label="Overdue" value={overdue.length}
                hint={oldestOverdue
                  ? `Oldest ${formatWeekdayDayMonth(oldestOverdue.next_date)}`
                  : undefined}
                className={cn("rounded-xl border bg-surface p-4 shadow-card",
                  overdue.length ? "border-negative/40" : "border-border")} />
              <Stat label="Annualised" value={<Money pence={monthlyTotal * 12} />}
                className="rounded-xl border border-border bg-surface p-4 shadow-card" />
              <Stat label="Paused" value={paused.length}
                className="rounded-xl border border-border bg-surface p-4 shadow-card" />
            </div>

            <div className="grid grid-cols-1 gap-4">
              {sections.map(([secName, rows]) => (
                <div key={secName || "ungrouped"} className="min-w-0">
                  {sectioned && (
                    <div className="mb-2 flex items-center gap-2.5 px-1">
                      {secName ? <IconTile label={secName} size="sm" /> : null}
                      <h2 className="text-[13px] font-semibold uppercase tracking-wide text-fg-subtle">
                        {secName || "Other bills"}
                      </h2>
                      <span className="text-[12px] text-fg-subtle">
                        {rows.length} · <Money pence={rows.reduce(
                          (s, c) => s + (c.amount_pence != null
                            ? monthlyPence(Math.abs(c.amount_pence), c.frequency) : 0), 0)} /> /mo
                      </span>
                    </div>
                  )}
                  <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
                    {rows.map((c) => {
                      const cat = c.category_id != null ? categories.byId.get(c.category_id) : undefined;
                      const acct = accountsById.get(c.account_id);
                      return (
                        <li key={c.id} id={`commitment-${c.id}`}
                          className="group flex min-w-0 flex-col rounded-[var(--radius-card)] border border-border bg-surface p-4 shadow-card">
                          <div className="mb-3 flex items-start gap-3">
                            <IconTile label={c.name} />
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-[14px] font-semibold">{c.name}</p>
                              <p className="truncate text-[12px] text-fg-subtle">
                                {cat?.name ?? "Uncategorised"}{acct ? ` · ${acct.name}` : ""}
                              </p>
                            </div>
                            {c.amount_pence != null ? (
                              <Money pence={c.amount_pence} signed tone="auto" className="text-[15px] font-semibold" />
                            ) : (
                              <Badge tone="info">formula</Badge>
                            )}
                          </div>
                          <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-fg-muted">
                            <span>{cadence(c)}</span>
                            <span className="inline-flex items-center gap-1">
                              <CalendarClock className="size-3.5" />
                              next {formatDayMonth(c.next_date)}
                            </span>
                            {c.occurrence_count != null && (
                              <span>{c.generated_count}/{c.occurrence_count} posted</span>
                            )}
                          </div>
                          {c.formula && (
                            <p className="ff-formula mb-3 truncate rounded-md bg-surface-2 px-2 py-1 font-mono text-[11.5px] text-fg-muted">
                              {c.formula}
                            </p>
                          )}
                          <div className="mt-auto flex items-center gap-1 border-t border-border pt-2.5">
                            <Button size="sm" variant="ghost" onClick={() => postNext(c)}
                              aria-label={`Post next ${c.name}`} title="Post the next occurrence as a transaction">
                              <Send /> Post next
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => setEditing(c)}
                              aria-label={`Edit ${c.name}`} title="Edit"><Pencil /></Button>
                            <Button size="sm" variant="ghost" onClick={() => pause(c)}
                              aria-label={`Pause ${c.name}`} title="Pause — stops generating"><Pause /></Button>
                            {!c.generated_count && (
                              <Button size="sm" variant="ghost" onClick={() => remove(c)}
                                aria-label={`Remove ${c.name}`} title="Remove permanently — never generated">
                                <Trash2 /></Button>
                            )}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))}
            </div>

            {paused.length > 0 && (
              <div className="mt-6">
                <h2 className="mb-2 px-1 text-[13px] font-semibold uppercase tracking-wide text-fg-subtle">
                  Paused ({paused.length})
                </h2>
                <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {paused.map((c) => (
                    <li key={c.id} id={`commitment-${c.id}`}
                      className="flex min-w-0 items-center gap-3 rounded-[var(--radius-card)] border border-dashed border-border bg-surface/60 p-4">
                      <IconTile label={c.name} className="opacity-60" />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-[14px] font-medium text-fg-muted">{c.name}</p>
                        <p className="text-[12px] text-fg-subtle">{cadence(c)}</p>
                      </div>
                      <Button size="sm" variant="ghost" onClick={() => resume(c)}
                        aria-label={`Resume ${c.name}`}><Play /> Resume</Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditing(c)}
                        aria-label={`Edit ${c.name}`}><Pencil /></Button>
                      {!c.generated_count && (
                        <Button size="sm" variant="ghost" onClick={() => remove(c)}
                          aria-label={`Remove ${c.name}`} title="Remove permanently — never generated">
                          <Trash2 /></Button>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {active.length === 0 && paused.length === 0 && (
              <EmptyState icon={ReceiptText} title="No bills yet"
                description="Add things you pay on a schedule — broadband, council tax, subscriptions — and they generate your upcoming movements automatically." />
            )}
          </>
        )}
      </DataView>

      {editing !== null && (
        <BillDialog
          commitment={editing === "new" ? null : editing}
          accounts={accounts.data?.items ?? []}
          categories={categories.data?.items ?? []}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); refresh(); }}
        />
      )}
    </div>
  );
}

/** How long the bill runs — the duration question the old form left
 * implicit behind two optional fields (issue #292). */
type Duration = "until_stopped" | "until_date" | "n_times";

/** Create/edit form — the core is name/amount/account/how often/how
 * long; formula, direction, day-of-month and notes live behind "More
 * options" so a plain bill is a five-field job. Exactly one of fixed
 * amount or formula, matching the backend invariant. */
function BillDialog({ commitment, accounts, categories, onClose, onSaved }: {
  commitment: Commitment | null;
  accounts: components["schemas"]["AccountOut"][];
  categories: Category[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const isNew = commitment === null;
  const [name, setName] = useState(commitment?.name ?? "");
  const [accountId, setAccountId] = useState(String(commitment?.account_id ?? ""));
  const [categoryId, setCategoryId] = useState(String(commitment?.category_id ?? ""));
  const [kind, setKind] = useState<"amount" | "formula">(commitment?.formula ? "formula" : "amount");
  const [pounds, setPounds] = useState(
    commitment?.amount_pence != null ? String(Math.abs(commitment.amount_pence) / 100) : "");
  const [formula, setFormula] = useState(commitment?.formula ?? "");
  const [frequency, setFrequency] = useState<Frequency>(commitment?.frequency ?? "monthly");
  const [dom, setDom] = useState(commitment?.day_of_month != null ? String(commitment.day_of_month) : "");
  const [nextDate, setNextDate] = useState(commitment?.next_date ?? new Date().toISOString().slice(0, 10));
  const [duration, setDuration] = useState<Duration>(
    commitment?.end_date ? "until_date"
      : commitment?.occurrence_count != null ? "n_times"
      : "until_stopped");
  const [endDate, setEndDate] = useState(commitment?.end_date ?? "");
  const [occurrences, setOccurrences] = useState(commitment?.occurrence_count != null ? String(commitment.occurrence_count) : "");
  const [notes, setNotes] = useState(commitment?.notes ?? "");
  const [out, setOut] = useState(commitment == null || (commitment.amount_pence ?? 0) <= 0);
  const [busy, setBusy] = useState(false);
  // Advanced opens by itself when the existing record uses it, or when
  // the user picks the formula kind.
  const [more, setMore] = useState(
    !isNew && (commitment!.formula != null
      || (commitment!.amount_pence ?? 0) > 0
      || commitment!.day_of_month != null
      || commitment!.notes != null
      || commitment!.category_id != null));

  const usable = categories.filter((c) => !c.archived);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    const body = {
      name: name.trim(),
      account_id: Number(accountId),
      category_id: categoryId ? Number(categoryId) : null,
      amount: kind === "amount"
        ? `${out ? "-" : ""}${pounds}` : null,
      formula: kind === "formula" ? formula.trim() : null,
      frequency,
      day_of_month: dom ? Number(dom) : null,
      next_date: nextDate,
      end_date: duration === "until_date" && endDate ? endDate : null,
      occurrence_count: duration === "n_times" && occurrences
        ? Number(occurrences) : null,
      notes: notes.trim() || null,
    };
    const req = isNew
      ? api.post("/api/v1/commitments", { body })
      : api.patch("/api/v1/commitments/{cid}", {
          path: { cid: commitment!.id },
          body: {
            name: body.name, category_id: body.category_id,
            amount: body.amount, formula: body.formula,
            frequency: body.frequency, day_of_month: body.day_of_month,
            next_date: body.next_date, end_date: body.end_date,
            occurrence_count: body.occurrence_count, notes: body.notes,
          },
        });
    req.then(() => {
        toast.success(isNew ? `${name.trim()} added` : `${name.trim()} saved`);
        onSaved();
      })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}
      title={isNew ? "Add a bill" : `Edit ${commitment!.name}`}
      description="A regular payment — it keeps coming up on schedule and can post real transactions."
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-bill"
          disabled={busy || !name.trim() || !accountId || (kind === "amount" ? !pounds : !formula.trim())}>
          {busy ? "Saving…" : isNew ? "Add bill" : "Save"}
        </Button>
      </>}>
      <form id="ff-bill" onSubmit={submit} className="grid gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name">{(id) => (
            <Input id={id} autoFocus required placeholder="e.g. Broadband"
              value={name} onChange={(e) => setName(e.target.value)} />
          )}</Field>
          <Field label="Paid from">{(id) => (
            <Select id={id} required value={accountId} onChange={(e) => setAccountId(e.target.value)}>
              <option value="" disabled>Choose account…</option>
              {accounts.filter((a) => !a.archived).map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </Select>
          )}</Field>
          <Field label="How much (£)">{(id) => (
            <Input id={id} required={kind === "amount"} type="number" step="0.01" min="0"
              aria-label="Amount in pounds"
              disabled={kind === "formula"}
              value={pounds} onChange={(e) => setPounds(e.target.value)} />
          )}</Field>
          <Field label="How often">{(id) => (
            <Select id={id} value={frequency} onChange={(e) => setFrequency(e.target.value as Frequency)}>
              <option value="weekly">Every week</option>
              <option value="fortnightly">Every 2 weeks</option>
              <option value="monthly">Every month</option>
              <option value="quarterly">Every 3 months</option>
              <option value="annually">Every year</option>
            </Select>
          )}</Field>
        </div>

        <Field label="For how long" hint="This repeats on schedule — pick when it should stop.">{(_id) => (
          <Segmented label="For how long" value={duration} onChange={setDuration}
            options={[
              { value: "until_stopped", label: "Until I stop it" },
              { value: "until_date", label: "Until a date" },
              { value: "n_times", label: "A set number of times" },
            ]} />
        )}</Field>
        {duration === "until_date" && (
          <Field label="Stops after">{(id) => (
            <Input id={id} required type="date" value={endDate}
              onChange={(e) => setEndDate(e.target.value)} />
          )}</Field>
        )}
        {duration === "n_times" && (
          <Field label="Stops after" hint="e.g. 12 — for a year of monthly payments.">{(id) => (
            <Input id={id} required type="number" min="1" aria-label="Number of payments"
              placeholder="e.g. 12"
              value={occurrences} onChange={(e) => setOccurrences(e.target.value)} />
          )}</Field>
        )}
        <Field label={duration === "until_stopped" ? "Next due" : "First due"}>{(id) => (
          <Input id={id} required type="date" value={nextDate} onChange={(e) => setNextDate(e.target.value)} />
        )}</Field>

        {/* Advanced: formula amounts, direction, anchor day, category,
         * notes — open when needed, quiet otherwise. */}
        <div>
          <button type="button" onClick={() => setMore((m) => !m)}
            aria-expanded={more}
            className="text-[12.5px] font-medium text-accent hover:underline">
            {more ? "Fewer options" : "More options — formula, direction, anchor day, category, notes"}
          </button>
        </div>
        {more && (
          <div className="grid gap-4 rounded-lg border border-border bg-surface-2/40 p-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Amount kind">{(id) => (
                <Select id={id} value={kind}
                  onChange={(e) => setKind(e.target.value as "amount" | "formula")}>
                  <option value="amount">Fixed amount</option>
                  <option value="formula">Formula (e.g. usage-based)</option>
                </Select>
              )}</Field>
              <Field label="Direction">{(id) => (
                <Select id={id} value={out ? "out" : "in"} onChange={(e) => setOut(e.target.value === "out")}>
                  <option value="out">Outgoing (a bill)</option>
                  <option value="in">Incoming (e.g. rent received)</option>
                </Select>
              )}</Field>
            </div>
            {kind === "formula" && (
              <Field label="Formula" hint="e.g. usage * 1.2 — resolved by the payroll/rules engine.">{(id) => (
                <Input id={id} required placeholder="usage * 1.2"
                  value={formula} onChange={(e) => setFormula(e.target.value)} />
              )}</Field>
            )}
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Category" hint="Groups it into a budget section.">{(id) => (
                <Select id={id} value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
                  <option value="">None</option>
                  {usable.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.parent_id != null ? `— ${c.name}` : c.name}
                    </option>
                  ))}
                </Select>
              )}</Field>
              <Field label="Same day each month" hint="Anchor the schedule, e.g. the 14th.">{(id) => (
                <Input id={id} type="number" min="1" max="31" placeholder="—"
                  value={dom} onChange={(e) => setDom(e.target.value)} />
              )}</Field>
            </div>
            <Field label="Notes">{(id) => (
              <Input id={id} placeholder="Optional" value={notes} onChange={(e) => setNotes(e.target.value)} />
            )}</Field>
          </div>
        )}
      </form>
    </Dialog>
  );
}
