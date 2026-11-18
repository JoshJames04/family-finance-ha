import { useState } from "react";
import { toast } from "sonner";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import type { components } from "../api/schema";
import { Button } from "./Button";
import { Dialog } from "./Dialog";
import { Field, Input, Select } from "./Field";
import { formatPence, formatSmartDate } from "../lib/format";
import { undoToast } from "../lib/toast";

export type SharePaymentMethod = "payroll_cash" | "payroll_noncash" | "outside_payroll";
export function SharePaymentPicker({ value, onChange }: { value: SharePaymentMethod; onChange: (value: SharePaymentMethod) => void }) {
  return <Field label="Share payment method" hint="How this employer handles the share part of a bonus.">{(id) => <Select id={id} className="min-h-11" value={value} onChange={(e) => onChange(e.target.value as SharePaymentMethod)}>
    <option value="payroll_cash">Cash paid and taxed through payroll</option>
    <option value="payroll_noncash">Non-cash award taxed through payroll</option>
    <option value="outside_payroll">Handled outside payroll (e.g. separate share sale)</option>
  </Select>}</Field>;
}

type Bonus = components["schemas"]["EmploymentBonusOut"];
const money = /^\d+(\.\d{1,2})?$/;
const percentage = /^\d+(\.\d{1,4})?$/;
const bonusBody = (bonus: Bonus) => ({
  pay_month: bonus.pay_month, label: bonus.label,
  salary_percentage: bonus.salary_percentage,
  share_value: bonus.share_value_pence == null ? null : String(bonus.share_value_pence / 100),
  manual_amount: String(bonus.manual_pence / 100),
});

export function SalaryBonuses({ employmentId, employer, sharePaymentMethod = "payroll_cash", rev, onChanged, hideAdd = false }: {
  employmentId: number; employer: string; sharePaymentMethod?: SharePaymentMethod; rev: number; onChanged: () => void; hideAdd?: boolean;
}) {
  const bonuses = useApi(() => api.get("/api/v1/employments/{emp_id}/bonuses", {
    path: { emp_id: employmentId },
  }), true, [employmentId, rev]);
  const [editing, setEditing] = useState<Bonus | "new" | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const changed = () => { bonuses.refetch(); onChanged(); };
  const remove = async (bonus: Bonus) => {
    setDeleting(bonus.id);
    setError(null);
    try {
      await api.delete("/api/v1/employments/{emp_id}/bonuses/{bonus_id}", {
        path: { emp_id: employmentId, bonus_id: bonus.id },
      });
      undoToast("Bonus removed", () => api.post("/api/v1/employments/{emp_id}/bonuses", {
        path: { emp_id: employmentId }, body: bonusBody(bonus),
      }).then(changed));
      changed();
    } catch (err) {
      setError((err as Error).message);
    } finally { setDeleting(null); }
  };
  const rows = [...(bonuses.data ?? [])].sort((a, b) => b.pay_month.localeCompare(a.pay_month) || b.id - a.id);
  return (
    <section className="mt-5 rounded-xl border border-border bg-surface-2/30 p-4" aria-label={`${employer} bonuses`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[13px] font-semibold">Bonuses</h3>
        {!hideAdd && <Button className="min-h-11" onClick={() => setEditing("new")}>Add bonus</Button>}
      </div>
      <p className="mt-1 text-[12.5px] text-fg-subtle">All entries in a month add together. See Breakdown for combined payroll deductions and monthly net pay.</p>
      {bonuses.loading && <p role="status" className="mt-2 text-[13px]">Loading bonuses…</p>}
      {bonuses.error && <p role="alert" className="mt-2 text-[13px]">{bonuses.error} <Button className="min-h-11" onClick={bonuses.refetch}>Retry bonuses</Button></p>}
      {error && <p role="alert" className="mt-2 text-[13px] text-negative">{error}</p>}
      {bonuses.data?.length === 0 && <p className="mt-2 text-[13px] text-fg-subtle">No bonuses recorded.</p>}
      <ul className="mt-2 divide-y divide-border">
        {rows.map((bonus) => <li key={bonus.id} className="py-3 text-[13px]">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div><p className="font-medium">{bonus.label}</p><p className="text-fg-subtle">Pay date {formatSmartDate(bonus.pay_date)} · {bonus.pay_month.slice(0, 7)}</p></div>
            <span className="font-semibold">{formatPence(bonus.gross_pence)} gross before deductions</span>
          </div>
          <dl className="mt-2 grid gap-1 text-[12.5px] text-fg-muted sm:grid-cols-3">
            <div><dt>Salary bonus</dt><dd>{bonus.salary_percentage ?? "0"}% × {formatPence(bonus.salary_basis_pence)} = {formatPence(bonus.salary_bonus_pence)}</dd></div>
            <div><dt>Share bonus</dt><dd>{bonus.shares_owned} shares × {formatPence(bonus.share_value_pence ?? 0)} = {formatPence(bonus.share_bonus_pence)}</dd></div>
            <div><dt>Manual bonus</dt><dd>{formatPence(bonus.manual_pence)}</dd></div>
          </dl>
          {bonus.noncash_pence > 0 && <p className="mt-1 text-[12.5px] text-fg-subtle">Includes {formatPence(bonus.noncash_pence)} non-cash shares, taxable through payroll and excluded from cash net pay.</p>}
          <div className="mt-2 flex flex-wrap justify-end gap-2">
            <Button className="min-h-11" variant="ghost" aria-label={`Edit ${bonus.label}`} onClick={() => setEditing(bonus)}>Edit</Button>
            <Button className="min-h-11" variant="ghost" aria-label={`Delete ${bonus.label}`} disabled={deleting !== null} onClick={() => remove(bonus)}>{deleting === bonus.id ? "Removing…" : "Delete"}</Button>
          </div>
        </li>)}
      </ul>
      {editing && <BonusDialog employmentId={employmentId} employer={employer} sharePaymentMethod={sharePaymentMethod} existing={editing === "new" ? undefined : editing}
        onClose={() => setEditing(null)} onSaved={changed} />}
    </section>
  );
}

export function BonusDialog({ employmentId, employer, sharePaymentMethod, existing, onClose, onSaved }: {
  employmentId: number; employer: string; sharePaymentMethod: SharePaymentMethod; existing?: Bonus; onClose: () => void; onSaved: () => void;
}) {
  const initial = existing ? bonusBody(existing) : null;
  const [month, setMonth] = useState(initial?.pay_month.slice(0, 7) ?? "");
  const [label, setLabel] = useState(initial?.label ?? "Bonus");
  const [salary, setSalary] = useState(initial?.salary_percentage ?? "");
  const [shareValue, setShareValue] = useState(initial?.share_value ?? "");
  const [manual, setManual] = useState(initial?.manual_amount ?? "");
  type BonusType = "salary" | "shares" | "manual";
  const [type, setType] = useState<BonusType>(!initial || Number(initial.salary_percentage) > 0 ? "salary" : Number(initial.share_value) > 0 ? "shares" : "manual");
  const mixed = !!initial && [initial.salary_percentage, initial.share_value, initial.manual_amount].filter(value => Number(value) > 0).length > 1;
  const changeType = (next: BonusType) => {
    if (!mixed) { setSalary(""); setShareValue(""); setManual(""); }
    setType(next);
  };
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const valid = /^\d{4}-(0[1-9]|1[0-2])$/.test(month) && label.trim()
    && (!salary || percentage.test(salary)) && (!shareValue || money.test(shareValue))
    && (!manual || money.test(manual)) && [salary, sharePaymentMethod === "outside_payroll" ? "" : shareValue, manual].some((value) => Number(value) > 0);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError(null);
    const body = { pay_month: `${month}-01`, label: label.trim(), salary_percentage: salary || null, share_value: sharePaymentMethod === "outside_payroll" ? null : shareValue || null, manual_amount: manual || "0" };
    try {
      if (existing) {
        await api.patch("/api/v1/employments/{emp_id}/bonuses/{bonus_id}", { path: { emp_id: employmentId, bonus_id: existing.id }, body });
      } else {
        await api.post("/api/v1/employments/{emp_id}/bonuses", { path: { emp_id: employmentId }, body });
      }
      toast.success("Bonus saved and payroll forecast updated");
      onSaved();
      onClose();
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };
  return (
    <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }} title={`${existing ? "Edit" : "Add"} bonus — ${employer}`}
      description="Choose a bonus type and pay month. Multiple entries in the same month add together before payroll deductions."
      footer={<><Button className="min-h-11" variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button><Button className="min-h-11" variant="primary" type="submit" form="ff-bonus" disabled={!valid || busy}>{busy ? "Saving…" : "Save bonus"}</Button></>}>
      <form id="ff-bonus" onSubmit={submit} className="grid gap-4">
        {error && <p role="alert" className="text-[13px] text-negative">{error}</p>}
        <Field label="Pay month" hint={existing ? "To move this bonus, delete it and add a new entry in the correct month." : undefined}>{(id) => <Input id={id} className="min-h-11" type="month" required disabled={Boolean(existing)} value={month} onChange={(e) => setMonth(e.target.value)} />}</Field>
        <Field label="Bonus label">{(id) => <Input id={id} className="min-h-11" maxLength={200} required value={label} onChange={(e) => setLabel(e.target.value)} />}</Field>
        <Field label="Bonus type">{(id) => <Select id={id} className="min-h-11" value={type} onChange={e => changeType(e.target.value as BonusType)}>
          <option value="salary">Salary percentage</option><option value="shares">Value per share</option><option value="manual">Manual gross amount</option>
        </Select>}</Field>
        {mixed && <p role="status" className="rounded-lg bg-surface-2 p-3 text-sm text-fg-muted">
          This existing entry combines several parts. Editing one keeps the others: salary {salary || "0"}%, shares £{shareValue || "0"} each, manual £{manual || "0"}.
        </p>}
        {type === "salary" && (<Field label="Salary percentage (%)" hint="Percentage of the effective annual salary. Optional, up to four decimal places.">{(id) => <Input id={id} className="min-h-11" inputMode="decimal" placeholder="0" value={salary} onChange={(e) => setSalary(e.target.value.trim())} />}</Field>)}
        {type === "shares" && (<Field label="Value per share (£)" hint={sharePaymentMethod === "outside_payroll" ? "Record separate share sales outside payroll. No PAYE or capital gains estimate is made here." : sharePaymentMethod === "payroll_noncash" ? "Non-cash award value per effective share held: taxable through payroll but excluded from cash net pay. Your normal pension settings apply." : "Cash paid and taxed through payroll for each share held on the pay date. Your normal pension settings apply."}>{(id) => <Input id={id} className="min-h-11" inputMode="decimal" placeholder="0.00" disabled={sharePaymentMethod === "outside_payroll"} value={sharePaymentMethod === "outside_payroll" ? "" : shareValue} onChange={(e) => setShareValue(e.target.value.trim())} />}</Field>)}
        {type === "manual" && (<Field label="Manual gross amount (£)">{(id) => <Input id={id} className="min-h-11" inputMode="decimal" value={manual} onChange={(e) => setManual(e.target.value.trim())} />}</Field>)}
        <p className="text-[12.5px] text-fg-subtle">Saving updates this month's payroll forecast. Existing bonus entries are kept and added together. Reconciled pay must be corrected before changing its bonuses.</p>
      </form>
    </Dialog>
  );
}
