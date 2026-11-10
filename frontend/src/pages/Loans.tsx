/** Loans: family lending both ways (we_owe / they_owe) — outstanding
 * position, repayment tracking, drift vs expected schedule and payoff
 * projection. */
import { useState } from "react";
import { toast } from "sonner";
import { ArrowDownLeft, ArrowUpRight, Handshake, Plus } from "lucide-react";
import { api } from "../api/client";
import { useApi, useMeProfileId } from "../api/useApi";
import { useReady } from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Panel } from "../components/Panel";
import { Badge } from "../components/Badge";
import { Money } from "../components/Money";
import { Button } from "../components/Button";
import { Dialog } from "../components/Dialog";
import { Checkbox, Field, Input, Select, Textarea } from "../components/Field";
import { PageHeader, Progress, Segmented, Stat } from "../components/Display";
import { Skeleton } from "../components/Feedback";
import {
  formatDayMonthYear, formatPence, formatSmartDate, toISODate,
} from "../lib/format";

type Loan = components["schemas"]["LoanOut"];
type Position = components["schemas"]["LoanPositionOut"];
type Repayment = components["schemas"]["RepaymentOut"];
type Direction = components["schemas"]["LoanDirection"];

export function Loans() {
  const ready = useReady();
  const [rev, setRev] = useState(0);
  const [dir, setDir] = useState<Direction | "all">("all");
  const [scope, setScope] = useState<"all" | "mine" | "shared">("all");
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Loan | null>(null);
  const [repaying, setRepaying] = useState<Loan | null>(null);
  const loans = useApi(
    () => api.get("/api/v1/loans", { query: { include_inactive: true } }),
    ready, [rev]);
  const profiles = useApi(() => api.get("/api/v1/profiles"), ready);
  const me = useMeProfileId();
  const memberCount = (profiles.data?.items ?? []).filter((p) => p.is_active).length;
  const items = (loans.data?.items ?? [])
    .filter((l) => dir === "all" || l.direction === dir)
    .filter((l) => scope === "all" ||
      (scope === "mine" ? l.owner_profile_id === me : l.owner_profile_id === null));
  const refresh = () => setRev((r) => r + 1);

  return (
    <div>
      <PageHeader
        title="Loans"
        description="Money lent to or borrowed from family — who's repaid what, and how the schedule is holding."
        actions={<Button variant="primary" onClick={() => setAdding(true)}><Plus /> New loan</Button>}
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Segmented
          label="Direction"
          value={dir}
          onChange={setDir}
          options={[
            { value: "all", label: "All" },
            { value: "they_owe", label: "Owed to us" },
            { value: "we_owe", label: "We owe" },
          ]}
        />
        {memberCount > 1 && (
          <Segmented
            label="Loan scope"
            value={scope}
            onChange={setScope}
            options={[
              { value: "all", label: "All" },
              { value: "mine", label: "Mine" },
              { value: "shared", label: "Shared" },
            ]}
          />
        )}
      </div>

      <DataView
        state={{ ...loans, data: items }}
        empty="No loans tracked"
        emptyIcon={Handshake}
        emptyDescription="Record money lent to or borrowed from family — repayments reconcile against the ledger."
        emptyAction={<Button variant="primary" onClick={() => setAdding(true)}><Plus /> New loan</Button>}
        loading={<div className="grid gap-3 lg:grid-cols-2">{[0, 1].map((i) => <Skeleton key={i} className="h-44 rounded-xl" />)}</div>}
      >
        {(list) => (
          <div className="grid gap-3 lg:grid-cols-2">
            {list.map((l) => (
              <LoanCard key={l.id} loan={l} rev={rev}
                owner={profiles.data?.items.find((p) => p.id === l.owner_profile_id)?.display_name ?? null}
                onEdit={() => setEditing(l)}
                onRepay={() => setRepaying(l)} />
            ))}
          </div>
        )}
      </DataView>

      <LoanDialog open={adding} onOpenChange={setAdding}
        profiles={profiles.data?.items ?? []} onSaved={refresh} />
      {editing && (
        <LoanDialog open onOpenChange={(o) => { if (!o) setEditing(null); }}
          profiles={profiles.data?.items ?? []} loan={editing} onSaved={refresh} />
      )}
      {repaying && (
        <RepayDialog loan={repaying} onOpenChange={(o) => { if (!o) setRepaying(null); }} onSaved={refresh} />
      )}
    </div>
  );
}

function LoanCard({ loan: l, rev, owner, onEdit, onRepay }: { loan: Loan; rev: number; owner: string | null; onEdit: () => void; onRepay: () => void }) {
  const position = useApi(
    () => api.get("/api/v1/loans/{lid}", { path: { lid: l.id } }),
    true, [l.id, rev]);
  const projection = useApi(
    () => api.get("/api/v1/loans/{lid}/projection", { path: { lid: l.id } }),
    true, [l.id, rev]);
  const repayments = useApi(
    () => api.get("/api/v1/loans/{lid}/repayments", { path: { lid: l.id } }),
    true, [l.id, rev]);
  const p: Position | null = position.data;
  const owed = l.direction === "they_owe";
  const drift = p?.drift_pence ?? 0;
  const behind = drift > 500; // expected-to-date exceeds actual by > £5
  const repaidPct = l.principal_pence > 0 && p
    ? Math.min(100, Math.round((p.repaid_pence / l.principal_pence) * 100)) : 0;
  return (
    <Panel className={l.active ? undefined : "opacity-60"}>
      <div className="flex items-start gap-3">
        <span className={`grid size-9 shrink-0 place-items-center rounded-xl ${owed ? "bg-positive-soft text-positive" : "bg-warning-soft text-warning"}`}>
          {owed ? <ArrowDownLeft className="size-4" /> : <ArrowUpRight className="size-4" />}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[15px] font-semibold">{l.party}</p>
          <p className="text-[12.5px] text-fg-subtle">
            {owed ? "They owe you" : "You owe them"} · from {formatDayMonthYear(l.start_date)}
            {l.interest_bps_pa ? ` · ${(l.interest_bps_pa / 100).toString()}% pa` : ""}
            {owner ? ` · ${owner}` : ""}
          </p>
        </div>
        {!l.active && <Badge tone="neutral">Settled</Badge>}
        {l.active && behind && <Badge tone="warning">Behind schedule</Badge>}
      </div>

      <div className="mt-4 grid grid-cols-3 gap-3">
        <Stat label="Outstanding"
          value={position.loading ? "…" : <Money pence={p?.outstanding_pence ?? l.principal_pence} />} />
        <Stat label="Repaid" value={position.loading ? "…" : <Money pence={p?.repaid_pence ?? 0} />}
          hint={`of ${formatPence(l.principal_pence)}`} />
        <Stat label="Payoff"
          value={projection.data?.baseline.payoff_date
            ? formatDayMonthYear(projection.data.baseline.payoff_date)
            : "—"}
          hint={projection.data?.baseline.months != null ? `in ${projection.data.baseline.months} mo` : undefined} />
      </div>

      <div className="mt-3">
        <Progress value={p?.repaid_pence ?? 0} max={l.principal_pence} tone="positive" label={`${l.party} repaid`} />
        <p className="mt-1 text-[12px] text-fg-subtle">{repaidPct}% repaid{l.expected_payment_pence ? ` · expected ${formatPence(l.expected_payment_pence)}/payment` : ""}</p>
      </div>

      {(repayments.data ?? []).length > 0 && (
        <details className="mt-3 border-t border-border pt-2.5">
          <summary className="cursor-pointer text-[12.5px] font-medium text-fg-muted hover:text-fg">
            {(repayments.data ?? []).length} repayment{(repayments.data ?? []).length === 1 ? "" : "s"}
          </summary>
          <ul className="mt-2 space-y-1.5">
            {(repayments.data ?? []).slice(0, 6).map((r: Repayment) => (
              <li key={r.id} className="flex items-center justify-between text-[12.5px]">
                <span className="text-fg-subtle">{formatSmartDate(r.date)}{r.notes ? ` · ${r.notes}` : ""}</span>
                <span className="tnum">
                  <Money pence={r.amount_pence} />
                  {r.balance_after_pence != null && <span className="text-fg-subtle"> → {formatPence(r.balance_after_pence)} left</span>}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="mt-3 flex justify-end gap-2 border-t border-border pt-3">
        <Button size="sm" variant="ghost" onClick={onEdit}>Edit</Button>
        {l.active && <Button size="sm" variant="secondary" onClick={onRepay}>Record repayment</Button>}
      </div>
    </Panel>
  );
}

function LoanDialog({ open, onOpenChange, profiles, loan, onSaved }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  profiles: components["schemas"]["ProfileOut"][];
  loan?: Loan | null;
  onSaved: () => void;
}) {
  const editing = loan ?? null;
  const [party, setParty] = useState(editing?.party ?? "");
  const [direction, setDirection] = useState<Direction>(editing?.direction ?? "they_owe");
  const [principal, setPrincipal] = useState(
    editing ? (editing.principal_pence / 100).toString() : "");
  const [expected, setExpected] = useState(
    editing?.expected_payment_pence != null
      ? (editing.expected_payment_pence / 100).toString() : "");
  const [startDate, setStartDate] = useState(editing?.start_date ?? toISODate(new Date()));
  const [interest, setInterest] = useState(
    editing?.interest_bps_pa != null
      ? (editing.interest_bps_pa / 100).toString() : "");
  const [ownerId, setOwnerId] = useState(
    editing?.owner_profile_id != null ? String(editing.owner_profile_id) : "");
  const [active, setActive] = useState(editing?.active ?? true);
  const [notes, setNotes] = useState(editing?.notes ?? "");
  const [busy, setBusy] = useState(false);
  const valid = party.trim() && /^\d+(\.\d{1,2})?$/.test(principal)
    && (interest === "" || /^\d+(\.\d{1,4})?$/.test(interest));
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    const interestBps = interest === "" ? null : Math.round(parseFloat(interest) * 100);
    const saved = editing
      ? api.patch("/api/v1/loans/{lid}", {
        path: { lid: editing.id },
        body: {
          party: party.trim(),
          direction,
          principal_pence: Math.round(parseFloat(principal) * 100),
          start_date: startDate,
          expected_payment_pence: expected === "" ? null : Math.round(parseFloat(expected) * 100),
          interest_bps_pa: interestBps,
          owner_profile_id: ownerId ? Number(ownerId) : null,
          active,
          notes: notes.trim() || null,
        },
      })
      : api.post("/api/v1/loans", {
        body: {
          party: party.trim(),
          direction,
          principal,
          start_date: startDate,
          expected_payment: expected || null,
          interest_bps_pa: interestBps,
          owner_profile_id: ownerId ? Number(ownerId) : null,
          notes: notes.trim() || null,
        },
      });
    saved
      .then(() => {
        toast.success(editing ? `Loan with ${party.trim()} updated` : `Loan with ${party.trim()} recorded`);
        setParty(""); setPrincipal(""); setExpected(""); setNotes("");
        onSaved();
        onOpenChange(false);
      })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}
      title={editing ? `Edit loan — ${editing.party}` : "New loan"}
      description={editing ? "Adjust the recorded loan; derived position stays computed." : "Track money lent to or borrowed from someone."}
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-add-loan" disabled={!valid || busy}>{busy ? "Saving…" : (editing ? "Save changes" : "Add loan")}</Button>
      </>}>
      <form id="ff-add-loan" onSubmit={submit} className="grid gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Who">{(id) => <Input id={id} autoFocus placeholder="e.g. Mum" value={party} onChange={(e) => setParty(e.target.value)} />}</Field>
          <Field label="Direction">{(id) => (
            <Select id={id} value={direction} onChange={(e) => setDirection(e.target.value as Direction)}>
              <option value="they_owe">They owe you</option>
              <option value="we_owe">You owe them</option>
            </Select>
          )}</Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Amount (£)">{(id) => <Input id={id} inputMode="decimal" placeholder="0.00" value={principal} onChange={(e) => setPrincipal(e.target.value.trim())} />}</Field>
          <Field label="Expected payment (£)" hint="Optional — enables schedule drift.">{(id) => <Input id={id} inputMode="decimal" placeholder="0.00" value={expected} onChange={(e) => setExpected(e.target.value.trim())} />}</Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Start date">{(id) => <Input id={id} type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />}</Field>
          <Field label="Interest % pa" hint="Optional — simple annual rate.">{(id) => <Input id={id} inputMode="decimal" placeholder="e.g. 5" value={interest} onChange={(e) => setInterest(e.target.value.trim())} />}</Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          {profiles.filter((p) => p.is_active).length > 1 && (
            <Field label="Whose loan" hint="Shared unless it's personal.">{(id) => (
              <Select id={id} aria-label="Loan owner" value={ownerId} onChange={(e) => setOwnerId(e.target.value)}>
                <option value="">Shared</option>
                {profiles.filter((p) => p.is_active).map((p) => (
                  <option key={p.id} value={p.id}>{p.display_name}</option>
                ))}
              </Select>
            )}</Field>
          )}
          {editing && (
            <Field label="Status" hint="Untick to mark the loan settled.">{(id) => (
              <label className="flex h-9 items-center gap-2 text-[13px]">
                <Checkbox id={id} checked={active} onChange={(e) => setActive(e.target.checked)} />
                Active
              </label>
            )}</Field>
          )}
        </div>
        <Field label="Notes">{(id) => <Textarea id={id} placeholder="Optional context" value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field>
      </form>
    </Dialog>
  );
}

function RepayDialog({ loan, onOpenChange, onSaved }: {
  loan: Loan;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const [date, setDate] = useState(toISODate(new Date()));
  const [amount, setAmount] = useState(
    loan.expected_payment_pence ? (loan.expected_payment_pence / 100).toString() : "");
  const [busy, setBusy] = useState(false);
  const valid = /^\d+(\.\d{1,2})?$/.test(amount);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    api.post("/api/v1/loans/{lid}/repayments", {
      path: { lid: loan.id },
      body: { date, amount },
    })
      .then(() => { toast.success("Repayment recorded"); onSaved(); onOpenChange(false); })
      .catch((err: Error) => { toast.error(err.message); setBusy(false); });
  };
  return (
    <Dialog open onOpenChange={onOpenChange} title={`Repayment — ${loan.party}`}
      description="Log a repayment against the outstanding balance."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Back</Button>
        <Button variant="primary" type="submit" form="ff-repay" disabled={!valid || busy}>{busy ? "Saving…" : "Record repayment"}</Button>
      </>}>
      <form id="ff-repay" onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        <Field label="Amount (£)">{(id) => <Input id={id} autoFocus inputMode="decimal" placeholder="0.00" value={amount} onChange={(e) => setAmount(e.target.value.trim())} />}</Field>
        <Field label="Date">{(id) => <Input id={id} type="date" value={date} onChange={(e) => setDate(e.target.value)} />}</Field>
      </form>
    </Dialog>
  );
}
