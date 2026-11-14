/** Overtime: log extra hours as draft entries, edit them while
 * they're drafts, then submit → approve for payroll. Rate and
 * multiplier are worked out from the employment's salary and its
 * configured multipliers — the workbook's Overtime sheet as a real
 * workflow. */
import { useRef, useState } from "react";
import { toast } from "sonner";
import { undoToast } from "../lib/toast";
import { Clock, Pencil, Plus, Trash2 } from "lucide-react";
import { api, ApiError } from "../api/client";
import { groupOvertimeWeeks, type OvertimeWeek } from "../lib/overtimeWeeks";
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
import { PageHeader } from "../components/Display";
import { Skeleton } from "../components/Feedback";
import {
  formatMultiplier, formatPence, formatSmartDate, toISODate, formatDayMonthYear as weekDate,
} from "../lib/format";

type Employment = components["schemas"]["EmploymentOut"];
type Entry = components["schemas"]["OvertimeOut"];
type DayType = components["schemas"]["DayType"];
type Status = components["schemas"]["OvertimeStatus"];

const STATUS_TONE: Record<Status, BadgeTone> = {
  draft: "neutral", submitted: "info", approved: "success",
  rejected: "warning",
};
const DAY_LABEL: Record<DayType, string> = {
  weekday: "Weekday", saturday: "Saturday", sunday: "Sunday",
  bank_holiday: "Bank holiday",
};
type WeekAction = "submit" | "approve" | "reject";
const WEEK_ACTIONS: { action: WeekAction; label: string; status: Status }[] = [
  { action: "submit", label: "Submit", status: "draft" },
  { action: "approve", label: "Approve", status: "submitted" },
  { action: "reject", label: "Reject", status: "submitted" },
];

export function Overtime() {
  const ready = useReady();
  const [rev, setRev] = useState(0);
  const [empId, setEmpId] = useState<number | null>(null);
  const [editing, setEditing] = useState<Entry | null>(null);
  const [adding, setAdding] = useState(false);
  const employments = useApi(() => api.get("/api/v1/employments"), ready, [rev]);
  const emps = employments.data?.items ?? [];
  const emp = emps.find((e) => e.id === empId) ?? emps[0] ?? null;
  const refresh = () => setRev((r) => r + 1);

  return (
    <div>
      <PageHeader
        title="Overtime"
        description="Log extra hours, get them approved, and feed the next payroll run."
        actions={emp && (
          <Button variant="primary" onClick={() => setAdding(true)}>
            <Plus /> New entry
          </Button>
        )}
      />
      <DataView
        state={{ ...employments, data: emps }}
        empty="No employments yet"
        emptyIcon={Clock}
        emptyDescription="Overtime belongs to an employment — set one up on the Salary page first."
        loading={<Skeleton className="h-48 rounded-xl" />}
      >
        {() => emp && (
          <>
            {emps.length > 1 && (
              <div className="mb-4 max-w-xs">
                <Field label="Employment">{(id) => (
                  <Select id={id} value={emp.id}
                    onChange={(e) => setEmpId(Number(e.target.value))}>
                    {emps.map((x) => (
                      <option key={x.id} value={x.id}>{x.employer}</option>
                    ))}
                  </Select>
                )}</Field>
              </div>
            )}
            <EntryList
              key={`${emp.id}:${rev}`}
              employment={emp}
              onEdit={setEditing}
              onChanged={refresh}
            />
          </>
        )}
      </DataView>

      {adding && emp && (
        <EntryDialog employment={emp}
          onOpenChange={(o) => { if (!o) setAdding(false); }}
          onSaved={refresh} />
      )}
      {editing && emp && (
        <EntryDialog employment={emp} existing={editing}
          onOpenChange={(o) => { if (!o) setEditing(null); }}
          onSaved={refresh} />
      )}
    </div>
  );
}

function EntryList({ employment, onEdit, onChanged }: {
  employment: Employment;
  onEdit: (e: Entry) => void;
  onChanged: () => void;
}) {
  const entries = useApi(
    () => api.get("/api/v1/employments/{emp_id}/overtime-entries",
      { path: { emp_id: employment.id } }),
    true, [employment.id]);
  const weeks = groupOvertimeWeeks(entries.data ?? []);
  const [confirmation, setConfirmation] = useState<{ week: OvertimeWeek; action: WeekAction } | null>(null);

  const act = (e: Entry, action: string, label: string) =>
    api.post(
      "/api/v1/employments/{emp_id}/overtime-entries/{entry_id}/{action}",
      { path: { emp_id: employment.id, entry_id: e.id, action } })
      .then(() => { toast.success(label); onChanged(); })
      .catch((err: Error) => toast.error(err.message));

  const remove = (e: Entry) =>
    api.delete("/api/v1/employments/{emp_id}/overtime-entries/{entry_id}",
      { path: { emp_id: employment.id, entry_id: e.id } })
      .then(() => {
        undoToast(
          `Overtime on ${formatSmartDate(e.work_date)} removed`, () =>
            api.post("/api/v1/employments/{emp_id}/overtime-entries", {
              path: { emp_id: employment.id },
              body: {
                work_date: e.work_date, hours: e.hours,
                multiplier_bps: e.multiplier_bps, day_type: e.day_type,
                notes: e.notes,
              },
            }).then(onChanged));
        onChanged();
      })
      .catch((err: Error) => toast.error(err.message));

  return (
    <DataView state={entries} empty="No overtime logged yet"
      emptyDescription="Add an entry for the extra hours worked."
      loading={<Skeleton className="h-48 rounded-xl" />}>
    {() => <div className="grid gap-4">
      {weeks.map(week => (
        <Panel key={week.start} flush
          title={`Week ${week.number}, ${week.year}`}
          description={`${weekDate(week.start)} – ${weekDate(week.end)}`}
          aria-label={`Week ${week.number}, ${week.year}`}
          actions={<div className="flex flex-wrap gap-2">
            {WEEK_ACTIONS.map(({ action, label, status }) => (
              <Button key={action} size="sm" variant={action === "submit" ? "primary" : "secondary"}
                className="min-h-11" disabled={week.counts[status] === 0}
                onClick={() => setConfirmation({ week, action })}>
                {label} week
              </Button>
            ))}
          </div>}>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-border px-4 py-3">
          <dl className="flex items-center gap-5 text-[13px]">
            <div><dt className="text-fg-subtle">Hours</dt><dd className="tnum font-semibold">{week.totalHours}h</dd></div>
            <div><dt className="text-fg-subtle">Pay</dt><dd className="tnum font-semibold"><Money pence={week.totalPayPence} /></dd></div>
          </dl>
          <div className="flex flex-wrap gap-2" aria-label="Entry statuses">
            {(Object.keys(STATUS_TONE) as Status[]).filter(status => week.counts[status] > 0).map(status => (
              <Badge key={status} tone={STATUS_TONE[status]}>
                {status.charAt(0).toUpperCase() + status.slice(1)}: {week.counts[status]}
              </Badge>
            ))}
          </div>
        </div>
        <p className="px-4 py-2 text-xs text-fg-subtle md:hidden">Scroll across the entries for individual actions.</p>
        <div className="relative overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable overtime entries">
        <table className="ff-table w-full text-[13.5px]" aria-label={`Entries for week ${week.number}, ${week.year}`}>
          <thead className="text-left text-[12px] text-fg-subtle">
            <tr className="h-9 border-b border-border">
              <th className="px-4 font-medium">Date</th>
              <th className="hidden px-3 font-medium sm:table-cell">Day</th>
              <th className="px-3 text-right font-medium">Hours</th>
              <th className="hidden px-3 text-right font-medium md:table-cell">Rate</th>
              <th className="hidden px-3 text-right font-medium md:table-cell">×</th>
              <th className="px-3 text-right font-medium">Pay</th>
              <th className="px-3 font-medium">Status</th>
              <th className="w-px pr-4"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {week.entries.map((e) => (
              <tr key={e.id} className="border-b border-border last:border-0">
                <td className="px-4 py-2.5 font-medium">
                  {formatSmartDate(e.work_date)}
                  {e.notes && !e.notes.startsWith("{") && (
                    <span className="block text-[11.5px] font-normal text-fg-subtle">{e.notes}</span>
                  )}
                </td>
                <td className="hidden px-3 text-fg-muted sm:table-cell">{DAY_LABEL[e.day_type]}</td>
                <td className="tnum px-3 text-right">{e.hours}h</td>
                <td className="tnum hidden px-3 text-right text-fg-muted md:table-cell">
                  {formatPence(e.hourly_rate_pence)}/hr
                </td>
                <td className="tnum hidden px-3 text-right text-fg-muted md:table-cell">
                  {formatMultiplier(e.multiplier_bps)}
                </td>
                <td className="tnum px-3 text-right font-medium"><Money pence={e.pay_pence} /></td>
                <td className="px-3"><Badge tone={STATUS_TONE[e.status]}>{e.status}</Badge></td>
                <td className="pr-4">
                  <div className="flex items-center justify-end gap-0.5">
                    <Button variant="ghost" size="icon-sm"
                      className="min-h-11 min-w-11"
                      aria-label={`Edit entry from ${formatSmartDate(e.work_date)}`}
                      onClick={() => onEdit(e)}>
                      <Pencil />
                    </Button>
                    {e.status === "draft" && (
                      <Button size="sm" variant="ghost" className="min-h-11"
                        onClick={() => act(e, "submit", "Submitted for approval")}>
                        Submit
                      </Button>
                    )}
                    {e.status === "submitted" && (
                      <>
                        <Button size="sm" variant="ghost" className="min-h-11"
                          onClick={() => act(e, "approve", "Approved — feeds payroll")}>
                          Approve
                        </Button>
                        <Button size="sm" variant="ghost" className="min-h-11"
                          onClick={() => act(e, "reject", "Rejected")}>
                          Reject
                        </Button>
                      </>
                    )}
                    {e.status === "rejected" && (
                      <Button size="sm" variant="ghost" className="min-h-11"
                        onClick={() => act(e, "rework", "Back to draft")}>
                        Rework
                      </Button>
                    )}
                    <Button variant="ghost" size="icon-sm"
                      className="min-h-11 min-w-11"
                      aria-label={`Delete entry from ${formatSmartDate(e.work_date)}`}
                      onClick={() => remove(e)}>
                      <Trash2 />
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
        </Panel>
      ))}
      {confirmation && <WeekConfirmation key={`${confirmation.week.start}:${confirmation.action}`}
        employmentId={employment.id} week={confirmation.week} action={confirmation.action}
        onClose={() => setConfirmation(null)} onChanged={onChanged} />}
    </div>}
    </DataView>
  );
}

function WeekConfirmation({ employmentId, week, action, onClose, onChanged }: {
  employmentId: number; week: OvertimeWeek; action: WeekAction;
  onClose: () => void; onChanged: () => void;
}) {
  const { label, status } = WEEK_ACTIONS.find(item => item.action === action)!;
  const eligible = week.entries.filter(entry => entry.status === status);
  const hours = eligible.reduce((sum, e) => sum + Math.round(Number(e.hours) * 100), 0) / 100;
  const pay = eligible.reduce((sum, entry) => sum + entry.pay_pence, 0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const inFlight = useRef(false);
  const stale = error instanceof ApiError && error.status === 409;
  const count = `${eligible.length} ${eligible.length === 1 ? "entry" : "entries"}`;
  const confirm = async () => {
    if (inFlight.current || stale) return;
    inFlight.current = true;
    setSaving(true);
    setError(null);
    try {
      const result = await api.post("/api/v1/employments/{emp_id}/overtime-weeks/{week_start}/{action}", {
        path: { emp_id: employmentId, week_start: week.start, action },
        body: { entries: eligible.map(({ id, revision }) => ({ id, revision })) },
      });
      toast.success(`${result.updated_count} overtime ${result.updated_count === 1 ? "entry" : "entries"} ${action === "submit" ? "submitted" : action === "approve" ? "approved" : "rejected"}`);
      onClose();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err : new Error("Could not update this week"));
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  };
  return <Dialog open onOpenChange={open => { if (!open && !inFlight.current) onClose(); }}
    title={`${label} week ${week.number}, ${week.year}`}
    description={`${weekDate(week.start)} – ${weekDate(week.end)}`}
    footer={<div className="flex w-full flex-wrap justify-end gap-2">
      <Button className="min-h-11" variant="secondary" onClick={onClose} disabled={saving}>Cancel</Button>
      {stale && <Button className="min-h-11" variant="secondary" onClick={() => { onClose(); onChanged(); }}>Refresh week</Button>}
      <Button className="min-h-11" variant="primary" disabled={saving || stale} onClick={() => void confirm()}>
        {saving ? "Saving…" : `${label} ${count}`}
      </Button>
    </div>}>
    <p className="mb-3 text-sm">{count} · {hours}h · <Money pence={pay} />. Only {status} entries will change; other entries stay as they are.</p>
    <div className="overflow-x-auto">
      <table className="ff-table w-full text-sm" aria-label={`Entries to ${action}`}>
        <thead><tr><th className="py-2 text-left">Date</th><th className="text-right">Hours</th><th className="text-right">Pay</th></tr></thead>
        <tbody>{eligible.map(entry => <tr key={entry.id} className="border-t border-border">
          <td className="py-2">{weekDate(entry.work_date)}</td><td className="tnum text-right">{entry.hours}h</td>
          <td className="tnum text-right"><Money pence={entry.pay_pence} /></td>
        </tr>)}</tbody>
      </table>
    </div>
    {error && <p role="alert" className="mt-3 text-sm text-negative">{error.message}</p>}
  </Dialog>;
}

/** New or edit form — date + hours are the inputs; the day type,
 * rate and multiplier all resolve from the date (weekends and UK
 * bank holidays included). Editing a submitted/approved entry
 * returns it to draft so it re-enters approval. */
function EntryDialog({ employment, existing, onOpenChange, onSaved }: {
  employment: Employment;
  existing?: Entry;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const [date, setDate] = useState(
    existing?.work_date ?? toISODate(new Date()));
  const [hours, setHours] = useState(existing ? String(existing.hours) : "");
  const [notes, setNotes] = useState(existing?.notes ?? "");
  const [busy, setBusy] = useState(false);
  const valid = date && /^\d+(\.\d{1,2})?$/.test(hours) && Number(hours) > 0
    && Number(hours) <= 24;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    const req = existing
      ? api.patch("/api/v1/employments/{emp_id}/overtime-entries/{entry_id}", {
          path: { emp_id: employment.id, entry_id: existing.id },
          body: {
            work_date: date,
            hours,
            notes: notes.trim() || null,
          },
        })
      : api.post("/api/v1/employments/{emp_id}/overtime-entries", {
          path: { emp_id: employment.id },
          body: {
            work_date: date,
            hours,
            notes: notes.trim() || null,
          },
        });
    req
      .then(() => {
        toast.success(existing ? "Entry updated" : "Overtime logged");
        onSaved();
        onOpenChange(false);
      })
      .catch((err: Error) => { toast.error(err.message); setBusy(false); });
  };

  return (
    <Dialog open onOpenChange={onOpenChange}
      title={`${existing ? "Edit" : "New"} overtime — ${employment.employer}`}
      description={existing && existing.status !== "draft"
        ? "Saving returns this entry to draft so it can be re-approved. Rate and day type re-derive from the date."
        : "The day type, rate and multiplier are worked out from the date — weekends and bank holidays included."}
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-overtime"
          disabled={!valid || busy}>
          {busy ? "Saving…" : existing ? "Save changes" : "Log overtime"}
        </Button>
      </>}>
      <form id="ff-overtime" onSubmit={submit} className="grid gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Date worked">{(id) => (
            <Input id={id} autoFocus required type="date" value={date}
              onChange={(e) => setDate(e.target.value)} />
          )}</Field>
          <Field label="Hours" hint="Up to 24 — e.g. 4 or 2.5.">{(id) => (
            <Input id={id} required inputMode="decimal" placeholder="e.g. 4"
              value={hours} onChange={(e) => setHours(e.target.value.trim())} />
          )}</Field>
        </div>
        <Field label="Note" hint={'Optional — e.g. "covered the early shift".'}>{(id) => (
          <Input id={id} value={notes} onChange={(e) => setNotes(e.target.value)} />
        )}</Field>
      </form>
    </Dialog>
  );
}
