/** Salary: employments with current pay figures, payroll run history
 * with per-component forecast-vs-actual breakdown, payslip reconcile
 * for forecast runs, and add-run / add-employment flows. */
import { useState } from "react";
import { toast } from "sonner";
import { Briefcase, ChevronDown, Plus } from "lucide-react";
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
import { IconTile, PageHeader, Stat } from "../components/Display";
import { Skeleton } from "../components/Feedback";
import { cn } from "../lib/cn";
import {
  formatDayMonth, formatPence, formatSmartDate, toISODate,
} from "../lib/format";

type Employment = components["schemas"]["EmploymentOut"];
type SalaryRevision = components["schemas"]["SalaryRevisionOut"];
type Run = components["schemas"]["PayrollRunOut"];
type Component = components["schemas"]["PayrollComponentOut"];
type RunStatus = components["schemas"]["PayrollRunStatus"];
type Profile = components["schemas"]["ProfileOut"];

const RUN_TONE: Record<RunStatus, BadgeTone> = {
  forecast: "info", reconciled: "success", corrected: "neutral",
};
const KIND_LABEL: Record<string, string> = {
  base_salary: "Base salary", overtime: "Overtime", bonus: "Bonus",
  allowance: "Allowance", unpaid_leave: "Unpaid leave", pension: "Pension",
  tax: "Tax", ni: "National Insurance", student_loan: "Student loan",
};
const JURISDICTION: Record<string, string> = {
  england_wales: "England & Wales", scotland: "Scotland", northern_ireland: "Northern Ireland",
};

export function Salary() {
  const ready = useReady();
  const [rev, setRev] = useState(0);
  const [addingEmp, setAddingEmp] = useState(false);
  const [addingRun, setAddingRun] = useState<Employment | null>(null);
  const employments = useApi(() => api.get("/api/v1/employments"), ready, [rev]);
  const profiles = useApi(() => api.get("/api/v1/profiles"), ready);
  const refresh = () => setRev((r) => r + 1);
  const items = employments.data?.items ?? [];

  return (
    <div>
      <PageHeader
        title="Salary"
        description="Employments, payroll runs and payslip breakdowns."
        actions={<Button variant="primary" onClick={() => setAddingEmp(true)}><Plus /> Add employment</Button>}
      />
      <DataView
        state={{ ...employments, data: items }}
        empty="No employments yet"
        emptyIcon={Briefcase}
        emptyDescription="Add an employment to model salary, deductions and payroll runs."
        emptyAction={<Button variant="primary" onClick={() => setAddingEmp(true)}><Plus /> Add employment</Button>}
        loading={<div className="flex flex-col gap-4">{[0, 1].map((i) => <Skeleton key={i} className="h-48 rounded-xl" />)}</div>}
      >
        {(list) => (
          <div className="flex flex-col gap-4">
            {list.map((e) => (
              <EmploymentCard
                key={e.id}
                employment={e}
                profileName={profiles.data?.items.find((p: Profile) => p.id === e.profile_id)?.display_name ?? null}
                rev={rev}
                onAddRun={() => setAddingRun(e)}
                onChanged={refresh}
              />
            ))}
          </div>
        )}
      </DataView>

      <AddEmploymentDialog open={addingEmp} onOpenChange={setAddingEmp} profiles={profiles.data?.items ?? []} onSaved={refresh} />
      {addingRun && <AddRunDialog employment={addingRun} onOpenChange={(o) => { if (!o) setAddingRun(null); }} onSaved={refresh} />}
    </div>
  );
}

function EmploymentCard({ employment: e, profileName, rev, onAddRun, onChanged }: {
  employment: Employment;
  profileName: string | null;
  rev: number;
  onAddRun: () => void;
  onChanged: () => void;
}) {
  const salary = useApi(
    () => api.get("/api/v1/employments/{emp_id}/salary",
      { path: { emp_id: e.id }, query: { on: toISODate(new Date()) } }),
    true, [e.id, rev]);
  const runs = useApi(
    () => api.get("/api/v1/employments/{emp_id}/payroll-runs",
      { path: { emp_id: e.id } }),
    true, [e.id, rev]);
  const revisions = useApi(
    () => api.get("/api/v1/employments/{emp_id}/salary-revisions",
      { path: { emp_id: e.id } }),
    true, [e.id, rev]);
  const [reconciling, setReconciling] = useState<Run | null>(null);
  const [salaryOpen, setSalaryOpen] = useState(false);
  const sorted = [...(runs.data ?? [])].sort((a, b) => b.pay_date.localeCompare(a.pay_date));
  const revs = [...(revisions.data ?? [])].sort(
    (a, b) => b.effective_date.localeCompare(a.effective_date));

  return (
    <Panel flush>
      <div className="flex flex-wrap items-center gap-3 px-4 py-3.5">
        <IconTile icon={Briefcase} label={e.employer} size="lg" />
        <div className="min-w-0 flex-1">
          <p className="text-[15px] font-semibold">{e.employer}</p>
          <p className="text-[12.5px] text-fg-subtle">
            {profileName ?? "—"} · {JURISDICTION[e.jurisdiction] ?? e.jurisdiction} · {e.contracted_hours}h/week
            {e.payday_rule ? ` · ${e.payday_rule}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-5">
          {salary.data && (
            <div className="text-right">
              <p className="tnum text-[17px] font-semibold"><Money pence={salary.data.annual_salary_pence} /></p>
              <p className="text-[11.5px] text-fg-subtle">per year · {formatPence(salary.data.hourly_rate_pence)}/hr</p>
            </div>
          )}
          <Button size="sm" variant="secondary" onClick={() => setSalaryOpen(true)}>
            New salary
          </Button>
          <Button size="sm" variant="secondary" onClick={onAddRun}><Plus /> Add run</Button>
        </div>
      </div>

      {/* Effective-dated salary history — same model the spreadsheet
       * used: a pay rise is a new figure from a date, never an edit
       * of the old one (issue #296). */}
      {(revs.length > 0 || !revisions.loading) && (
        <div className="border-t border-border px-4 py-2.5">
          <ul className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[12.5px]">
            {revs.slice(0, 4).map((r, i) => (
              <li key={r.id} className="flex items-baseline gap-1.5">
                <Money pence={r.annual_salary_pence}
                  className={i === 0 ? "font-semibold" : "text-fg-muted"} />
                <span className="text-fg-subtle">
                  from {formatSmartDate(r.effective_date)}
                  {r.notes && !r.notes.startsWith("{") ? ` · ${r.notes}` : ""}
                </span>
              </li>
            ))}
            {revs.length === 0 && (
              <li className="text-fg-subtle">
                No salary recorded yet — add the figure and the date it started.
              </li>
            )}
          </ul>
        </div>
      )}

      <div className="border-t border-border">
        {(runs.data ?? []).length === 0 ? (
          <p className="px-4 py-6 text-center text-[13px] text-fg-subtle">
            {runs.loading ? "Loading runs…" : "No payroll runs yet — add one to forecast payday."}
          </p>
        ) : (
          <table className="ff-table w-full text-[13.5px]">
            <thead className="text-left text-[12px] text-fg-subtle">
              <tr className="h-9 border-b border-border">
                <th className="w-8 pl-4"><span className="sr-only">Expand</span></th>
                <th className="px-3 font-medium">Period</th>
                <th className="hidden px-3 font-medium sm:table-cell">Pay date</th>
                <th className="px-3 text-right font-medium">Gross</th>
                <th className="hidden px-3 text-right font-medium sm:table-cell">Net</th>
                <th className="px-3 font-medium">Status</th>
                <th className="w-px pr-4"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => (
                <RunRow key={r.id} run={r} empId={e.id} onReconcile={() => setReconciling(r)} />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {reconciling && (
        <ReconcileDialog
          run={reconciling}
          empId={e.id}
          onOpenChange={(o) => { if (!o) setReconciling(null); }}
          onSaved={onChanged}
        />
      )}
      {salaryOpen && (
        <SalaryRevisionDialog
          employment={e}
          onOpenChange={(o) => { if (!o) setSalaryOpen(false); }}
          onSaved={onChanged}
        />
      )}
    </Panel>
  );
}

/** Record a salary change effective from a date — a pay rise, a new
 * rate on contract change (issue #296). Revisions are a dated series;
 * the engine picks the one current on any given day. */
function SalaryRevisionDialog({ employment, onOpenChange, onSaved }: {
  employment: Employment;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const [date, setDate] = useState(toISODate(new Date()));
  const [pounds, setPounds] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const valid = date && /^\d+(\.\d{1,2})?$/.test(pounds) && Number(pounds) >= 0;
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    api.post("/api/v1/employments/{emp_id}/salary-revisions", {
      path: { emp_id: employment.id },
      body: {
        effective_date: date,
        annual_salary: pounds,
        notes: notes.trim() || null,
      },
    })
      .then(() => {
        toast.success(`Salary updated from ${formatSmartDate(date)}`);
        onSaved();
        onOpenChange(false);
      })
      .catch((err: Error) => { toast.error(err.message); setBusy(false); });
  };
  return (
    <Dialog open onOpenChange={onOpenChange}
      title={`New salary — ${employment.employer}`}
      description="The annual figure and the date it takes effect. Past salary stays on record."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-salaryrev"
          disabled={!valid || busy}>{busy ? "Saving…" : "Save salary"}</Button>
      </>}>
      <form id="ff-salaryrev" onSubmit={submit} className="grid gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Annual salary (£)">{(id) => (
            <Input id={id} autoFocus inputMode="decimal" placeholder="e.g. 42000"
              value={pounds} onChange={(e) => setPounds(e.target.value.trim())} />
          )}</Field>
          <Field label="Effective from" hint="The payslip it first appears on.">{(id) => (
            <Input id={id} required type="date" value={date}
              onChange={(e) => setDate(e.target.value)} />
          )}</Field>
        </div>
        <Field label="Note" hint={'Optional — e.g. "April pay rise".'}>{(id) => (
          <Input id={id} value={notes} onChange={(e) => setNotes(e.target.value)} />
        )}</Field>
      </form>
    </Dialog>
  );
}

function RunRow({ run: r, empId, onReconcile }: { run: Run; empId: number; onReconcile: () => void }) {
  const [open, setOpen] = useState(false);
  const detail = useApi(
    () => api.get("/api/v1/employments/{emp_id}/payroll-runs/{run_id}",
      { path: { emp_id: empId, run_id: r.id } }),
    open, [open]);
  const components = detail.data?.components ?? r.components ?? [];
  return (
    <>
      <tr
        className="cursor-pointer border-b border-border hover:bg-surface-2/60"
        onClick={() => setOpen((o) => !o)}
      >
        <td className="pl-4 text-fg-subtle">
          <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} />
        </td>
        <td className="px-3 py-2.5 font-medium">
          {formatDayMonth(r.period_start)} – {formatSmartDate(r.period_end)}
          <span className="ml-2 text-[11.5px] text-fg-subtle">{r.tax_year}</span>
        </td>
        <td className="hidden px-3 text-fg-muted sm:table-cell">{formatSmartDate(r.pay_date)}</td>
        <td className="tnum px-3 text-right font-medium"><Money pence={r.gross_pence} /></td>
        <td className="tnum hidden px-3 text-right sm:table-cell"><Money pence={r.net_pence} /></td>
        <td className="px-3"><Badge tone={RUN_TONE[r.status]}>{r.status}</Badge></td>
        <td className="pr-4 text-right">
          {r.status === "forecast" && (
            <Button size="sm" variant="ghost" onClick={(e) => { e.stopPropagation(); onReconcile(); }}>
              Reconcile
            </Button>
          )}
        </td>
      </tr>
      {open && (
        <tr className="border-b border-border bg-surface-2/40">
          <td colSpan={7} className="px-4 py-3">
            {detail.loading ? (
              <Skeleton className="h-16 w-full" />
            ) : components.length === 0 ? (
              <p className="text-[12.5px] text-fg-subtle">No component breakdown recorded.</p>
            ) : (
              <div className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
                {components.map((c: Component) => (
                  <div key={c.id} className="flex items-center justify-between gap-3 rounded-lg border border-border bg-surface px-3 py-2 text-[12.5px]">
                    <span className="font-medium">{KIND_LABEL[c.kind] ?? c.kind}<span className="block text-[11px] font-normal text-fg-subtle">{c.label}</span></span>
                    <span className="tnum text-right">
                      {c.actual_pence !== null ? (
                        <>
                          <Money pence={c.actual_pence} className="font-medium" />
                          {c.variance_pence !== null && c.variance_pence !== 0 && (
                            <span className="block"><Money pence={c.variance_pence} signed tone="variance" /></span>
                          )}
                        </>
                      ) : (
                        <Money pence={c.forecast_pence} className="text-fg-muted" />
                      )}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function ReconcileDialog({ run, empId, onOpenChange, onSaved }: {
  run: Run;
  empId: number;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const components = run.components ?? [];
  const [actuals, setActuals] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    const body: Record<string, number> = {};
    for (const [k, v] of Object.entries(actuals)) {
      if (v.trim()) body[k] = Math.round(Number(v) * 100);
    }
    api.post("/api/v1/employments/{emp_id}/payroll-runs/{run_id}/reconcile", {
      path: { emp_id: empId, run_id: run.id },
      body: { actuals: body },
    })
      .then(() => { toast.success("Run reconciled against payslip"); onSaved(); onOpenChange(false); })
      .catch((err: Error) => { toast.error(err.message); setBusy(false); });
  };
  return (
    <Dialog open onOpenChange={onOpenChange}
      title={`Reconcile payslip — ${formatDayMonth(run.pay_date)}`}
      description="Enter the actual figures from the payslip. Anything left blank is assumed correct."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-reconcile" disabled={busy}>{busy ? "Reconciling…" : "Reconcile"}</Button>
      </>}>
      <form id="ff-reconcile" onSubmit={submit} className="grid gap-3">
        {components.length === 0 && (
          <p className="text-[13px] text-fg-subtle">No components on this run — confirm to mark it reconciled as-is.</p>
        )}
        {components.map((c) => (
          <div key={c.id} className="grid grid-cols-[1fr_140px] items-center gap-3">
            <span className="text-[13px]">
              {KIND_LABEL[c.kind] ?? c.kind}
              <span className="block text-[11.5px] text-fg-subtle">forecast {formatPence(c.forecast_pence)}</span>
            </span>
            <Input
              sizeVariant="sm"
              inputMode="decimal"
              aria-label={`Actual ${KIND_LABEL[c.kind] ?? c.kind}`}
              placeholder={formatPence(c.forecast_pence)}
              value={actuals[c.kind] ?? ""}
              onChange={(e) => setActuals((s) => ({ ...s, [c.kind]: e.target.value.trim() }))}
            />
          </div>
        ))}
      </form>
    </Dialog>
  );
}

function AddRunDialog({ employment, onOpenChange, onSaved }: {
  employment: Employment;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [payDate, setPayDate] = useState("");
  const [bonus, setBonus] = useState("");
  const [busy, setBusy] = useState(false);
  const valid = start && end && payDate;
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    api.post("/api/v1/employments/{emp_id}/payroll-runs", {
      path: { emp_id: employment.id },
      body: {
        period_start: start,
        period_end: end,
        pay_date: payDate,
        bonus: bonus || null,
      },
    })
      .then(() => { toast.success("Payroll run added"); onSaved(); onOpenChange(false); })
      .catch((err: Error) => { toast.error(err.message); setBusy(false); });
  };
  return (
    <Dialog open onOpenChange={onOpenChange} title={`New payroll run — ${employment.employer}`}
      description="The engine computes gross, tax, NI and deductions for the period."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-add-run" disabled={!valid || busy}>{busy ? "Adding…" : "Add run"}</Button>
      </>}>
      <form id="ff-add-run" onSubmit={submit} className="grid gap-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Period start">{(id) => <Input id={id} type="date" value={start} onChange={(e) => setStart(e.target.value)} />}</Field>
          <Field label="Period end">{(id) => <Input id={id} type="date" value={end} onChange={(e) => setEnd(e.target.value)} />}</Field>
          <Field label="Pay date">{(id) => <Input id={id} type="date" value={payDate} onChange={(e) => setPayDate(e.target.value)} />}</Field>
        </div>
        <Field label="One-off bonus (£)" hint="Optional.">{(id) => <Input id={id} inputMode="decimal" placeholder="0.00" value={bonus} onChange={(e) => setBonus(e.target.value.trim())} />}</Field>
      </form>
    </Dialog>
  );
}

function AddEmploymentDialog({ open, onOpenChange, profiles, onSaved }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  profiles: Profile[];
  onSaved: () => void;
}) {
  const [profileId, setProfileId] = useState("");
  const [employer, setEmployer] = useState("");
  const [hours, setHours] = useState("37.5");
  const [pounds, setPounds] = useState("");
  const [effDate, setEffDate] = useState(toISODate(new Date()));
  const [busy, setBusy] = useState(false);
  const pid = profileId || String(profiles[0]?.id ?? "");
  const valid = employer.trim() && pid && Number(hours) > 0;
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    api.post("/api/v1/employments", {
      body: {
        profile_id: Number(pid),
        employer: employer.trim(),
        contracted_hours: hours,
        jurisdiction: "england_wales",
        ni_category: "A",
        student_loan_plan: "none",
        pension_mode: "none",
        pension_employee_bps: 0,
        pension_fixed_pence: 0,
        work_days_per_week: "5",
      },
    })
      .then(async (emp) => {
        // A starting salary becomes the first effective-dated
        // revision — same thing a pay rise adds later (#296).
        if (pounds && /^\d+(\.\d{1,2})?$/.test(pounds) && emp?.id != null) {
          await api.post("/api/v1/employments/{emp_id}/salary-revisions", {
            path: { emp_id: emp.id },
            body: { effective_date: effDate, annual_salary: pounds },
          }).catch((err: Error) => toast.error(
            `Employment added, salary not saved: ${err.message}`));
        }
        toast.success(`Added ${employer.trim()}`);
        setEmployer("");
        setPounds("");
        onSaved();
        onOpenChange(false);
      })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Add employment"
      description="A job for a household member — salary is recorded as what it is and when it started."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-add-emp" disabled={!valid || busy}>{busy ? "Adding…" : "Add employment"}</Button>
      </>}>
      <form id="ff-add-emp" onSubmit={submit} className="grid gap-4">
        <Field label="Member">{(id) => (
          <Select id={id} value={pid} onChange={(e) => setProfileId(e.target.value)}>
            {profiles.map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}
          </Select>
        )}</Field>
        <Field label="Employer">{(id) => <Input id={id} autoFocus placeholder="e.g. Acme Ltd" value={employer} onChange={(e) => setEmployer(e.target.value)} />}</Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Contracted hours / week">{(id) => <Input id={id} inputMode="decimal" value={hours} onChange={(e) => setHours(e.target.value.trim())} />}</Field>
          <Field label="Annual salary (£)" hint="Optional — add it later from the card.">{(id) => (
            <Input id={id} inputMode="decimal" placeholder="e.g. 42000"
              value={pounds} onChange={(e) => setPounds(e.target.value.trim())} />
          )}</Field>
        </div>
        {pounds && (
          <Field label="Salary effective from">{(id) => (
            <Input id={id} type="date" value={effDate}
              onChange={(e) => setEffDate(e.target.value)} />
          )}</Field>
        )}
      </form>
    </Dialog>
  );
}
