/** Overtime: log extra hours as draft entries, edit them while
 * they're drafts, then submit → approve for payroll. Rate and
 * multiplier are worked out from the employment's salary and its
 * configured multipliers — the workbook's Overtime sheet as a real
 * workflow. */
import { useState } from "react";
import { toast } from "sonner";
import { undoToast } from "../lib/toast";
import { Clock, Pencil, Plus, Trash2 } from "lucide-react";
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
import { PageHeader } from "../components/Display";
import { Skeleton } from "../components/Feedback";
import {
  formatMultiplier, formatPence, formatSmartDate, toISODate,
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
  const rows = [...(entries.data ?? [])]
    .sort((a, b) => b.work_date.localeCompare(a.work_date));

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
    <Panel flush>
      {entries.loading ? (
        <div className="flex flex-col gap-2 p-4">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-9" />)}
        </div>
      ) : rows.length === 0 ? (
        <p className="px-4 py-10 text-center text-[13.5px] text-fg-subtle">
          No overtime logged yet — add an entry for the extra hours worked.
        </p>
      ) : (
        <div className="overflow-x-auto">
        <table className="ff-table w-full text-[13.5px]">
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
            {rows.map((e) => (
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
                      aria-label={`Edit entry from ${formatSmartDate(e.work_date)}`}
                      onClick={() => onEdit(e)}>
                      <Pencil />
                    </Button>
                    {e.status === "draft" && (
                      <Button size="sm" variant="ghost"
                        onClick={() => act(e, "submit", "Submitted for approval")}>
                        Submit
                      </Button>
                    )}
                    {e.status === "submitted" && (
                      <>
                        <Button size="sm" variant="ghost"
                          onClick={() => act(e, "approve", "Approved — feeds payroll")}>
                          Approve
                        </Button>
                        <Button size="sm" variant="ghost"
                          onClick={() => act(e, "reject", "Rejected")}>
                          Reject
                        </Button>
                      </>
                    )}
                    {e.status === "rejected" && (
                      <Button size="sm" variant="ghost"
                        onClick={() => act(e, "rework", "Back to draft")}>
                        Rework
                      </Button>
                    )}
                    <Button variant="ghost" size="icon-sm"
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
      )}
    </Panel>
  );
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
