/** Salary: employments with current pay figures, payroll run history
 * with per-component forecast-vs-actual breakdown, payslip reconcile
 * for forecast runs, and add-run / add-employment flows. */
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { undoToast } from "../lib/toast";
import { Briefcase, ChevronDown, Pencil, Plus, Trash2 } from "lucide-react";
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
type Delivery = components["schemas"]["DeliveryOut"];
type RunStatus = components["schemas"]["PayrollRunStatus"];
type Profile = components["schemas"]["ProfileOut"];

type PaydayKind = "fixed_day" | "nth_weekday" | "weekly" | "nightly" | "legacy";
type PaydayValue = {
  kind: PaydayKind;
  day: number | null;
  weekday: number | null;
  nth: number | null;
  adjust: "previous" | "next" | "none";
  raw?: string;
};

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const PAYDAY_KINDS: Array<{ value: PaydayKind; label: string }> = [
  { value: "fixed_day", label: "Fixed day of the month" },
  { value: "nth_weekday", label: "Last named weekday of the month" },
  { value: "weekly", label: "Weekly named weekday" },
  { value: "nightly", label: "Nightly" },
];

function parsePayday(raw: string | null | undefined): PaydayValue | null {
  if (!raw) return null;
  if (raw === "last_friday") {
    return { kind: "nth_weekday", day: null, weekday: 4, nth: -1, adjust: "previous", raw };
  }
  try {
    const parsed = JSON.parse(raw) as Partial<PaydayValue>;
    if (typeof parsed.kind !== "string"
      || !PAYDAY_KINDS.some((item) => item.value === parsed.kind)) {
      return { kind: "legacy", day: null, weekday: null, nth: null, adjust: "previous", raw };
    }
    return {
      kind: parsed.kind as PaydayKind,
      day: parsed.day ?? null,
      weekday: parsed.weekday ?? null,
      nth: parsed.nth ?? null,
      adjust: parsed.adjust ?? "previous",
      raw,
    };
  } catch {
    return { kind: "legacy", day: null, weekday: null, nth: null, adjust: "previous", raw };
  }
}

function serializePayday(value: PaydayValue | null): string | null {
  if (!value) return null;
  if (value.kind === "legacy" && value.raw) return value.raw;
  return JSON.stringify({
    adjust: value.adjust,
    day: value.day,
    kind: value.kind,
    nth: value.nth,
    weekday: value.weekday,
  });
}

function paydayLabel(raw: string | null | undefined): string | null {
  const value = parsePayday(raw);
  if (!value) return null;
  if (value.kind === "fixed_day" && value.day) {
    return `${value.day}${value.day === 1 ? "st" : value.day === 2 ? "nd" : value.day === 3 ? "rd" : "th"} of each month (previous Friday at weekends)`;
  }
  if (value.kind === "nth_weekday" && value.weekday !== null) {
    return `Last ${WEEKDAYS[value.weekday]} of each month`;
  }
  if (value.kind === "weekly" && value.weekday !== null) {
    return `Every ${WEEKDAYS[value.weekday]}`;
  }
  if (value.kind === "nightly") return "Nightly";
  if (raw === "last_working_day") return "Last working day of each month";
  if (raw === "last_day") return "Last day of each month";
  try {
    const kind = (JSON.parse(raw ?? "") as { kind?: string }).kind;
    if (kind === "last_working_day") return "Last working day of each month";
    if (kind === "last_day") return "Last day of each month";
  } catch {
    // Preserve unknown legacy values without surfacing JSON in the card.
  }
  return raw && raw.startsWith("{") ? "Existing payday rule" : raw ?? null;
}

function PaydayPicker({ value, onChange }: {
  value: string;
  onChange: (value: string) => void;
}) {
  const parsed = parsePayday(value);
  const kind = parsed?.kind ?? "";
  const update = (patch: Partial<PaydayValue>) => {
    const next: PaydayValue = {
      kind: kind as PaydayKind,
      day: parsed?.day ?? 25,
      weekday: parsed?.weekday ?? 4,
      nth: parsed?.nth ?? -1,
      adjust: parsed?.adjust ?? "previous",
      ...patch,
    };
    onChange(serializePayday(next) ?? "");
  };
  return (
    <div className="grid gap-3">
      <Field label="Payday" hint="Numeric dates move to the previous Friday when they fall on a weekend.">
        {(id) => (
          <Select id={id} value={kind}
            onChange={(e) => {
              if (!e.target.value) {
                onChange("");
                return;
              }
              update({
                kind: e.target.value as PaydayKind,
                day: e.target.value === "fixed_day" ? (parsed?.day ?? 25) : null,
                weekday: e.target.value === "nightly" ? null : (parsed?.weekday ?? 4),
                nth: e.target.value === "nth_weekday" ? -1 : null,
                adjust: e.target.value === "fixed_day" ? "previous" : "none",
              });
            }}>
            <option value="">No payday rule</option>
            {PAYDAY_KINDS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            {parsed?.kind === "legacy" && <option value="legacy">Existing legacy rule</option>}
          </Select>
        )}
      </Field>
      {kind === "fixed_day" && (
        <Field label="Day of month">{(id) => (
          <Input id={id} type="number" min={1} max={31}
            value={parsed?.day ?? 25}
            onChange={(e) => update({ day: Math.max(1, Math.min(31, Number(e.target.value) || 1)) })} />
        )}</Field>
      )}
      {(kind === "nth_weekday" || kind === "weekly") && (
        <Field label="Weekday">{(id) => (
          <Select id={id} value={String(parsed?.weekday ?? 4)}
            onChange={(e) => update({ weekday: Number(e.target.value) })}>
            {WEEKDAYS.map((day, index) => <option key={day} value={index}>{day}</option>)}
          </Select>
        )}</Field>
      )}
      {kind === "legacy" && (
        <p className="text-[12.5px] text-fg-subtle">
          Existing rule: {paydayLabel(value)}. Leave unchanged to preserve it, or choose a new rule above.
        </p>
      )}
    </div>
  );
}

function employmentBadge(e: Employment): { label: string; tone: BadgeTone } {
  if (!e.end_date) return { label: "active", tone: "success" };
  return e.end_date < toISODate(new Date())
    ? { label: `ended ${formatSmartDate(e.end_date)}`, tone: "neutral" }
    : { label: `ends ${formatSmartDate(e.end_date)}`, tone: "warning" };
}

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
function runAmounts(run: Run) {
  // Authoritative totals come from the API: baseline run fields plus signed
  // component deltas — legacy partial component lists can't understate pay.
  return {
    forecastGross: run.gross_pence,
    forecastNet: run.net_pence,
    actualGross: run.actual_gross_pence ?? null,
    actualNet: run.actual_net_pence ?? null,
  };
}

function activeRuns(runs: Run[]) {
  const superseded = new Set(
    runs.map((run) => run.corrects_id).filter((id): id is number => id !== null));
  return runs.filter((run) => run.status !== "corrected" && !superseded.has(run.id));
}

export function Salary() {
  const ready = useReady();
  const [rev, setRev] = useState(0);
  const [tab, setTab] = useState<"employers" | "breakdown" | "worklog">("employers");
  const [addingEmp, setAddingEmp] = useState(false);
  const [addingRun, setAddingRun] = useState<Employment | null>(null);
  const employments = useApi(() => api.get("/api/v1/employments"), ready, [rev]);
  const profiles = useApi(() => api.get("/api/v1/profiles"), ready);
  const refresh = () => setRev((r) => r + 1);
  const items = employments.data?.items ?? [];
  const today = toISODate(new Date());
  const pieceworks = items.filter((e) => e.employment_type === "piecework"
    && (e.start_date ?? "") <= today
    && (!e.end_date || e.end_date >= today));
  const [workEmploymentId, setWorkEmploymentId] = useState<number | null>(null);
  const piecework = pieceworks.find((e) => e.id === workEmploymentId)
    ?? pieceworks[0];
  const employmentIds = items.map((e) => e.id).join(",");

  const runsAll = useApi(
    () => Promise.all(items.map((e) =>
      api.get("/api/v1/employments/{emp_id}/payroll-runs",
        { path: { emp_id: e.id } }))),
    ready && items.length > 0, [rev, employmentIds]);
  const horizon = (runsAll.data ?? []).flat()
    .reduce<string | null>(
      (m, r) => (m === null || r.pay_date > m ? r.pay_date : m), null);
  return (
    <div>
      <PageHeader
        title="Salaries"
        description="Employers, salary history and pay breakdowns."
        actions={
          <Button variant="primary" onClick={() => setAddingEmp(true)}>
            <Plus /> Add employer
          </Button>
        }
      />
      <div className="mb-5 flex gap-1 border-b border-border">
        {(["employers", "breakdown", ...(piecework ? ["worklog" as const] : [])]).map((t) => (
          <button key={t} type="button" onClick={() => setTab(t as "employers" | "breakdown" | "worklog")}
            className={cn(
              "border-b-2 px-3 py-2 text-[13.5px] font-medium capitalize transition-colors",
              tab === t
                ? "border-accent text-fg"
                : "border-transparent text-fg-muted hover:text-fg")}>
            {t === "employers" ? "Employers" : t === "breakdown" ? "Breakdown" : "Work log"}
          </button>
        ))}
      </div>
      {tab === "breakdown" && (
        <MonthlyPayrollSummary runs={(runsAll.data ?? []).flat()} />
      )}
      <DataView
        state={{ ...employments, data: items }}
        empty="No employments yet"
        emptyIcon={Briefcase}
        emptyDescription="Add an employer to model salary, deductions and payroll runs."
        emptyAction={<Button variant="primary" onClick={() => setAddingEmp(true)}><Plus /> Add employer</Button>}
        loading={<div className="flex flex-col gap-4">{[0, 1].map((i) => <Skeleton key={i} className="h-48 rounded-xl" />)}</div>}
      >
        {(list) => tab === "employers" ? (
          <EmployersView employments={list} rev={rev} onChanged={refresh} />
        ) : tab === "worklog" && piecework ? (
          <div className="grid gap-3">
            {pieceworks.length > 1 && (
              <Field label="Piecework employer">{(id) => (
                <Select id={id} value={String(piecework.id)}
                  onChange={(e) => setWorkEmploymentId(Number(e.target.value))}>
                  {pieceworks.map((emp) => <option key={emp.id} value={emp.id}>{emp.employer}</option>)}
                </Select>
              )}</Field>
            )}
            <WorkLogView employment={piecework} onChanged={refresh} />
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {list.map((e) => (
              <EmploymentCard
                key={e.id}
                employment={e}
                profileName={profiles.data?.items.find((p: Profile) => p.id === e.profile_id)?.display_name ?? null}
                rev={rev}
                onAddRun={() => setAddingRun(e)}
                showSalaryActions={false}
                onChanged={refresh}
              />
            ))}
          </div>
        )}
      </DataView>

      <AddEmploymentDialog open={addingEmp} onOpenChange={setAddingEmp}
        profiles={profiles.data?.items ?? []}
        employments={items} onSaved={refresh} />
      {addingRun && <AddRunDialog employment={addingRun} onOpenChange={(o) => { if (!o) setAddingRun(null); }} onSaved={refresh} />}
    </div>
  );
}

function WorkLogView({ employment, onChanged }: {
  employment: Employment;
  onChanged: () => void;
}) {
  const [rev, setRev] = useState(0);
  const deliveries = useApi(
    () => api.get("/api/v1/employments/{emp_id}/deliveries",
      { path: { emp_id: employment.id } }),
    true, [employment.id, rev]);
  const [date, setDate] = useState(toISODate(new Date()));
  const [hours, setHours] = useState("");
  const [hourlyRate, setHourlyRate] = useState("");
  const [tips, setTips] = useState("");
  const [charges, setCharges] = useState([{ amount: "", label: "" }]);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const valid = Boolean(date && (Number(hours) > 0 || charges.some((c) => Number(c.amount) >= 0 && c.amount !== "")));
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!valid) return;
    setBusy(true);
    const request = editingId === null
      ? api.post("/api/v1/employments/{emp_id}/deliveries", {
        path: { emp_id: employment.id },
        body: {
          work_date: date,
          hours: hours || "0",
          hourly_rate: hourlyRate || null,
          deliveries: 0,
          tips: tips || null,
          charges: charges.filter((c) => c.amount !== "").map((c) => ({
            amount: c.amount, label: c.label || null,
          })),
        },
      })
      : api.patch("/api/v1/employments/{emp_id}/deliveries/{entry_id}", {
        path: { emp_id: employment.id, entry_id: editingId },
        body: {
          work_date: date,
          hours: hours || "0",
          hourly_rate: hourlyRate || null,
          deliveries: 0,
          tips: tips || null,
          charges: charges.filter((c) => c.amount !== "").map((c) => ({
            amount: c.amount, label: c.label || null,
          })),
        },
      });
    request
      .then(() => {
        toast.success(editingId === null ? "Work shift added" : "Work shift updated");
        setEditingId(null);
        setHours(""); setHourlyRate(""); setTips("");
        setCharges([{ amount: "", label: "" }]);
        setRev((value) => value + 1);
        onChanged();
      }).catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };
  const edit = (row: Delivery) => {
    setEditingId(row.id);
    setDate(row.work_date);
    setHours(String(row.hours));
    setHourlyRate(String(row.hourly_rate_pence / 100));
    setTips(String(row.tips_pence / 100));
    setCharges(row.charges.length
      ? row.charges.map((charge) => ({
        amount: String(charge.amount_pence / 100), label: charge.label ?? "",
      }))
      : [{ amount: "", label: "" }]);
  };
  const remove = (row: Delivery) => api.delete(
    "/api/v1/employments/{emp_id}/deliveries/{entry_id}", {
      path: { emp_id: employment.id, entry_id: row.id },
    }).then(() => {
      toast.success("Work shift removed");
      setRev((value) => value + 1);
      onChanged();
    }).catch((err: Error) => toast.error(err.message));
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <Panel title={`Work log — ${employment.employer}`}
        description="Record the hourly base and each delivery charge. Logged work is not treated as a recurring forecast.">
        <form onSubmit={submit} className="grid gap-3">
          <Field label="Work date">{(id) => <Input id={id} required type="date" value={date} onChange={(e) => setDate(e.target.value)} />}</Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Hours">{(id) => <Input id={id} inputMode="decimal" value={hours} onChange={(e) => setHours(e.target.value)} />}</Field>
            <Field label="Hourly rate (£)">{(id) => <Input id={id} inputMode="decimal" value={hourlyRate} onChange={(e) => setHourlyRate(e.target.value)} />}</Field>
          </div>
          <Field label="Tips (£)" hint="Optional, kept separate from delivery charges.">{(id) => <Input id={id} inputMode="decimal" value={tips} onChange={(e) => setTips(e.target.value)} />}</Field>
          <div className="grid gap-2">
            <span className="text-[13px] font-medium">Delivery charges</span>
            {charges.map((charge, index) => (
              <div key={index} className="grid grid-cols-[1fr_1fr_auto] gap-2">
                <Input aria-label={`Charge ${index + 1} amount`} inputMode="decimal"
                  placeholder="Amount (£)" value={charge.amount}
                  onChange={(e) => setCharges((rows) => rows.map((row, i) => i === index ? { ...row, amount: e.target.value } : row))} />
                <Input aria-label={`Charge ${index + 1} label`} placeholder="Label (optional)"
                  value={charge.label}
                  onChange={(e) => setCharges((rows) => rows.map((row, i) => i === index ? { ...row, label: e.target.value } : row))} />
                <Button type="button" variant="ghost" size="icon-sm" aria-label={`Remove charge ${index + 1}`}
                  onClick={() => setCharges((rows) => rows.length === 1 ? rows : rows.filter((_, i) => i !== index))}>×</Button>
              </div>
            ))}
            <Button type="button" size="sm" variant="ghost"
              onClick={() => setCharges((rows) => [...rows, { amount: "", label: "" }])}>Add delivery charge</Button>
          </div>
          <Button type="submit" variant="primary" disabled={!valid || busy}>
            {busy ? "Saving…" : editingId === null ? "Add shift" : "Save shift"}
          </Button>
          {editingId !== null && <Button type="button" variant="ghost"
            onClick={() => { setEditingId(null); setHours(""); setHourlyRate(""); setTips(""); setCharges([{ amount: "", label: "" }]); }}>
            Cancel edit
          </Button>}
        </form>
      </Panel>
      <Panel title="Recorded shifts" description="Imported shifts are marked for review and retain their source amount.">
        <DataView state={{ ...deliveries, data: deliveries.data ?? [] }}
          empty="No work logged yet" loading={<Skeleton className="h-24 rounded-xl" />}>
          {(rows) => <ul className="divide-y divide-border">
            {[...rows].reverse().map((row: Delivery) => (
              <li key={row.id} className="flex items-center justify-between gap-3 py-2 text-[13px]">
                <span><span className="block font-medium">{formatSmartDate(row.work_date)}</span>
                  <span className="text-[12px] text-fg-subtle">{row.hours}h · {row.charges?.length ?? 0} charges · <Money pence={Math.round(Number(row.hours) * row.hourly_rate_pence) + (row.tips_pence ?? 0) + row.charges.reduce((sum, charge) => sum + charge.amount_pence, 0)} /></span></span>
                <span className="flex items-center gap-1">
                  <Button size="sm" variant="ghost" onClick={() => edit(row)}>Edit</Button>
                  <Button size="sm" variant="ghost" onClick={() => remove(row)}>Delete</Button>
                {row.needs_review && <Badge tone="warning">needs review</Badge>}
                </span>
              </li>
            ))}
          </ul>}
        </DataView>
      </Panel>
    </div>
  );
}

function MonthlyPayrollSummary({ runs }: { runs: Run[] }) {
  const groups = new Map<string, Run[]>();
  for (const run of activeRuns(runs)) {
    const key = run.period_start.slice(0, 7);
    groups.set(key, [...(groups.get(key) ?? []), run]);
  }
  const months = [...groups.entries()].sort(([a], [b]) => b.localeCompare(a));
  if (!months.length) return null;
  return (
    <section aria-label="Monthly payroll summary" className="mb-5">
      <h2 className="mb-2 text-[12px] font-medium uppercase tracking-wide text-fg-subtle">
        Monthly payroll summary
      </h2>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {months.map(([month, monthRuns]) => {
          const totals = monthRuns.reduce((acc, run) => {
            const values = runAmounts(run);
            acc.forecastGross += values.forecastGross;
            acc.forecastNet += values.forecastNet;
            if (values.actualGross !== null && values.actualNet !== null) {
              acc.actualGross += values.actualGross;
              acc.actualNet += values.actualNet;
              acc.effectiveGross += values.actualGross;
              acc.effectiveNet += values.actualNet;
              acc.actualRuns++;
            } else {
              acc.effectiveGross += values.forecastGross;
              acc.effectiveNet += values.forecastNet;
            }
            return acc;
          }, {
            forecastGross: 0, forecastNet: 0, actualGross: 0, actualNet: 0,
            effectiveGross: 0, effectiveNet: 0, actualRuns: 0,
          });
          const mixed = totals.actualRuns > 0 && totals.actualRuns < monthRuns.length;
          return (
            <div key={month} className="rounded-xl border border-border bg-surface px-4 py-3">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[13px] font-medium">{month}</span>
                <Badge tone={mixed ? "warning" : totals.actualRuns ? "success" : "info"}>
                  {mixed ? "mixed actual / forecast" : totals.actualRuns ? "actual" : "forecast"}
                </Badge>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-3 text-[12px]">
                <span><span className="block text-fg-subtle">Effective gross</span>
                  <Money pence={totals.effectiveGross} />
                  {mixed && <span className="block text-fg-subtle">actual <Money pence={totals.actualGross} /> · forecast <Money pence={totals.forecastGross} /></span>}</span>
                <span><span className="block text-fg-subtle">Effective net</span>
                  <Money pence={totals.effectiveNet} />
                  {mixed && <span className="block text-fg-subtle">actual <Money pence={totals.actualNet} /> · forecast <Money pence={totals.forecastNet} /></span>}</span>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

/** Employers tab: 50/50 — employer list left, the selected
 * employer's salary history and settings right. */
function EmployersView({ employments, rev, onChanged }: {
  employments: Employment[];
  rev: number;
  onChanged: () => void;
}) {
  const [selId, setSelId] = useState<number | null>(null);
  const sel = employments.find((e) => e.id === selId)
    ?? employments.find((e) => !e.end_date) ?? employments[0];
  const [ending, setEnding] = useState(false);
  const [endDate, setEndDate] = useState("");
  const [deleting, setDeleting] = useState(false);
  const remove = (e: Employment) =>
    api.delete("/api/v1/employments/{emp_id}", { path: { emp_id: e.id } })
      .then(() => { toast.success(`Removed ${e.employer}`); setDeleting(false); onChanged(); })
      .catch((err: Error) => toast.error(err.message));
  const saveEnd = (e: Employment) =>
    api.patch("/api/v1/employments/{emp_id}", {
      path: { emp_id: e.id }, body: { end_date: endDate || null } })
      .then(() => { toast.success(`${e.employer} ends ${endDate}`); setEnding(false); onChanged(); })
      .catch((err: Error) => toast.error(err.message));

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel flush>
        <ul className="divide-y divide-border">
          {employments.map((e) => (
            <li key={e.id}>
              <button type="button" onClick={() => setSelId(e.id)}
                className={cn(
                  "flex w-full items-center gap-3 px-4 py-3 text-left transition-colors",
                  sel?.id === e.id ? "bg-surface-2/60" : "hover:bg-surface-2/40")}>
                <IconTile icon={Briefcase} label={e.employer} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[14px] font-medium">{e.employer}</span>
                  <span className="block text-[12px] text-fg-subtle">
                    {e.contracted_hours}h/week
                  </span>
                </span>
                <Badge tone={employmentBadge(e).tone}>{employmentBadge(e).label}</Badge>
              </button>
            </li>
          ))}
        </ul>
      </Panel>
      {sel && (
        <EmployerDetail key={sel.id} employment={sel} ending={ending}
          endDate={endDate} setEndDate={setEndDate}
          onEnding={() => { setEndDate(sel.end_date ?? ""); setEnding(true); }}
          onCancelEnd={() => setEnding(false)}
          onSaveEnd={() => saveEnd(sel)}
          onDelete={() => setDeleting(true)} rev={rev} onChanged={onChanged} />
      )}
      {sel && deleting && (
        <Dialog open onOpenChange={(open) => { if (!open) setDeleting(false); }}
          title={`Delete ${sel.employer}?`}
          description="This removes the employment and its salary history. Employers with payroll history must be ended instead."
          footer={<>
            <Button variant="ghost" onClick={() => setDeleting(false)}>Cancel</Button>
            <Button variant="primary" onClick={() => remove(sel)}>Delete employment</Button>
          </>}>
          <p className="text-[13px]">Confirm that you want to permanently remove this employer.</p>
        </Dialog>
      )}
    </div>
  );
}

function EmployerDetail({ employment: e, ending, endDate, setEndDate,
  onEnding, onCancelEnd, onSaveEnd, onDelete, rev, onChanged }: {
  employment: Employment;
  ending: boolean; endDate: string;
  setEndDate: (v: string) => void;
  onEnding: () => void; onCancelEnd: () => void;
  onSaveEnd: () => void; onDelete: () => void;
  rev: number;
  onChanged: () => void;
}) {
  const revisions = useApi(
    () => api.get("/api/v1/employments/{emp_id}/salary-revisions",
      { path: { emp_id: e.id } }),
    true, [e.id, rev]);
  const [editingRev, setEditingRev] = useState<SalaryRevision | null>(null);
  const [salaryOpen, setSalaryOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [endConfirmed, setEndConfirmed] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const revs = [...(revisions.data ?? [])].sort(
    (a, b) => b.effective_date.localeCompare(a.effective_date));
  const removeRevision = (r: SalaryRevision) =>
    api.delete("/api/v1/employments/{emp_id}/salary-revisions/{rev_id}",
      { path: { emp_id: e.id, rev_id: r.id } })
      .then(() => {
        undoToast(`Salary of ${formatPence(r.annual_salary_pence)} removed`, () =>
          api.post("/api/v1/employments/{emp_id}/salary-revisions", {
            path: { emp_id: e.id },
            body: {
              effective_date: r.effective_date,
              annual_salary: String(r.annual_salary_pence / 100),
              notes: r.notes,
            },
          }).then(onChanged));
        onChanged();
      })
      .catch((err: Error) => toast.error(err.message));

  return (
    <Panel>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-[16px] font-semibold">{e.employer}</h2>
          <p className="mt-0.5 text-[12.5px] text-fg-subtle">
            {JURISDICTION[e.jurisdiction] ?? e.jurisdiction} · {e.contracted_hours}h/week
            {e.payday_rule ? ` · ${paydayLabel(e.payday_rule)}` : ""}
            {e.end_date ? ` · ${employmentBadge(e).label}` : ""}
          </p>
          {e.deduction_mode === "gross_only" && (
            <Badge tone="warning" className="mt-2">gross-only — tax is not estimated</Badge>
          )}
        </div>
        <div className="flex gap-2">
          <Button size="icon-sm" variant="ghost"
            aria-label={`Edit ${e.employer}`} onClick={() => setEditing(true)}><Pencil /></Button>
          {!ending && !e.end_date && (
            <Button size="sm" variant="secondary" onClick={onEnding}>End employment</Button>
          )}
          <Button size="sm" variant="secondary" onClick={() => setSalaryOpen(true)}>New salary</Button>
          {e.employment_type === "piecework" && (
            <Button size="sm" variant="secondary" onClick={() => setImportOpen(true)}>
              Review imported history
            </Button>
          )}
          <Button size="icon-sm" variant="ghost" aria-label={`Delete ${e.employer}`}
            onClick={onDelete}><Trash2 /></Button>
        </div>
      </div>
      {ending && (
        <div className="mt-3 flex flex-wrap items-end gap-2 rounded-lg border border-border bg-surface-2/40 p-3">
          <Field label="Last day of employment">{(id) => (
            <Input id={id} type="date" value={endDate}
              onChange={(ev) => setEndDate(ev.target.value)} />
          )}</Field>
          <label className="flex items-center gap-2 text-[12px]">
            <input type="checkbox" checked={endConfirmed}
              onChange={(ev) => setEndConfirmed(ev.target.checked)} />
            Confirm this is the last day of employment
          </label>
          <Button size="sm" variant="primary" disabled={!endDate || !endConfirmed}
            onClick={onSaveEnd}>Save end date</Button>
          <Button size="sm" variant="ghost" onClick={onCancelEnd}>Cancel</Button>
        </div>
      )}
      <h3 className="mt-4 text-[12px] font-medium uppercase tracking-wide text-fg-subtle">Salary history</h3>
      <ul className="mt-1.5 flex flex-col gap-1 text-[13px]">
        {revs.map((r, i) => (
          <li key={r.id} className="flex items-baseline gap-1.5">
            <Money pence={r.annual_salary_pence}
              className={i === 0 ? "font-semibold" : "text-fg-muted"} />
            <span className="text-fg-subtle">
              from {formatSmartDate(r.effective_date)}
              {r.notes && !r.notes.startsWith("{") ? ` · ${r.notes}` : ""}
            </span>
            <span className="flex items-center gap-0.5">
              <Button variant="ghost" size="icon-sm"
                aria-label={`Edit salary from ${formatSmartDate(r.effective_date)}`}
                onClick={() => setEditingRev(r)}><Pencil /></Button>
              {revs.length > 1 && (
                <Button variant="ghost" size="icon-sm"
                  aria-label={`Delete salary from ${formatSmartDate(r.effective_date)}`}
                  onClick={() => removeRevision(r)}><Trash2 /></Button>
              )}
            </span>
          </li>
        ))}
        {revs.length === 0 && !revisions.loading && (
          <li className="text-fg-subtle">No salary recorded yet.</li>
        )}
      </ul>
      {salaryOpen && (
        <SalaryRevisionDialog employment={e}
          onOpenChange={(o) => { if (!o) setSalaryOpen(false); }}
          onSaved={onChanged} />
      )}
      {editingRev && (
        <SalaryRevisionDialog employment={e} existing={editingRev}
          onOpenChange={(o) => { if (!o) setEditingRev(null); }}
          onSaved={onChanged} />
      )}
      {editing && (
        <EditEmploymentDialog employment={e}
          onOpenChange={(o) => { if (!o) setEditing(false); }}
          onSaved={onChanged} />
      )}
      {importOpen && (
        <PieceworkImportDialog employment={e}
          onOpenChange={(open) => { if (!open) setImportOpen(false); }}
          onSaved={onChanged} />
      )}
    </Panel>
  );
}

/** Edit an employment's own details — name, contracted hours and
 * end date (clearable, e.g. resuming after a mistaken end). Salary
 * figures stay in the dated revision history above. */
function EditEmploymentDialog({ employment: e, onOpenChange, onSaved }: {
  employment: Employment;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const [employer, setEmployer] = useState(e.employer);
  const [hours, setHours] = useState(String(e.contracted_hours));
  const [endDate, setEndDate] = useState(e.end_date ?? "");
  const [paydayRule, setPaydayRule] = useState(e.payday_rule ?? "");
  const [employmentType, setEmploymentType] = useState<"salaried" | "piecework">(
    e.employment_type === "piecework" ? "piecework" : "salaried");
  const [deductionMode, setDeductionMode] = useState<"paye" | "gross_only">(
    e.deduction_mode === "gross_only" ? "gross_only" : "paye");
  const [busy, setBusy] = useState(false);
  const valid = employer.trim() && Number(hours) > 0;
  const submit = (ev: React.FormEvent) => {
    ev.preventDefault();
    if (!valid) return;
    setBusy(true);
    api.patch("/api/v1/employments/{emp_id}", {
      path: { emp_id: e.id },
      body: {
        employer: employer.trim(),
        contracted_hours: hours,
        end_date: endDate || null,
        payday_rule: paydayRule || null,
        employment_type: employmentType,
        deduction_mode: deductionMode,
      },
    })
      .then(() => { toast.success(`${e.employer} updated`); onSaved(); onOpenChange(false); })
      .catch((err: Error) => { toast.error(err.message); setBusy(false); });
  };
  return (
    <Dialog open onOpenChange={onOpenChange} title={`Edit ${e.employer}`}
      description="Correct the employer name, contracted hours or the end date. Salary figures live in the dated history below."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-edit-emp"
          disabled={!valid || busy}>{busy ? "Saving…" : "Save changes"}</Button>
      </>}>
      <form id="ff-edit-emp" onSubmit={submit} className="grid gap-4">
        <Field label="Employer">{(id) => (
          <Input id={id} autoFocus value={employer}
            onChange={(ev) => setEmployer(ev.target.value)} />
        )}</Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Contracted hours / week">{(id) => (
            <Input id={id} inputMode="decimal" value={hours}
              onChange={(ev) => setHours(ev.target.value.trim())} />
          )}</Field>
          <Field label="End date" hint="Blank = still employed.">{(id) => (
            <Input id={id} type="date" value={endDate}
              onChange={(ev) => setEndDate(ev.target.value)} />
          )}</Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Employment type">{(id) => (
            <Select id={id} value={employmentType}
              onChange={(ev) => setEmploymentType(ev.target.value as "salaried" | "piecework")}>
              <option value="salaried">Salaried</option>
              <option value="piecework">Piecework</option>
            </Select>
          )}</Field>
          <Field label="Tax deduction mode"
            hint="Gross-only is not tax exempt; record actual tax manually.">{(id) => (
            <Select id={id} value={deductionMode}
              onChange={(ev) => setDeductionMode(ev.target.value as "paye" | "gross_only")}>
              <option value="paye">PAYE</option>
              <option value="gross_only">Gross-only / cash in hand</option>
            </Select>
          )}</Field>
        </div>
        <PaydayPicker value={paydayRule} onChange={setPaydayRule} />
      </form>
    </Dialog>
  );
}

function EmploymentCard({ employment: e, profileName, rev, onAddRun, showSalaryActions = true, onChanged }: {
  employment: Employment;
  profileName: string | null;
  rev: number;
  onAddRun: () => void;
  showSalaryActions?: boolean;
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
  const [reconciling, setReconciling] = useState<Run | null>(null);
  const [payslipRun, setPayslipRun] = useState<Run | null>(null);
  const [salaryOpen, setSalaryOpen] = useState(false);
  const sorted = [...(runs.data ?? [])].sort((a, b) => b.pay_date.localeCompare(a.pay_date));

  return (
    <Panel flush>
      <div className="flex flex-wrap items-center gap-3 px-4 py-3.5">
        <IconTile icon={Briefcase} label={e.employer} size="lg" />
        <div className="min-w-0 flex-1">
          <p className="text-[15px] font-semibold">{e.employer}</p>
          <p className="text-[12.5px] text-fg-subtle">
            {profileName ?? "—"} · {JURISDICTION[e.jurisdiction] ?? e.jurisdiction} · {e.contracted_hours}h/week
            {e.payday_rule ? ` · ${paydayLabel(e.payday_rule)}` : ""}
          </p>
          {e.deduction_mode === "gross_only" && (
            <Badge tone="warning" className="mt-2">gross-only — tax is not estimated</Badge>
          )}
        </div>
        <div className="flex items-center gap-5">
          {salary.data && (
            <div className="text-right">
              <p className="tnum text-[17px] font-semibold"><Money pence={salary.data.annual_salary_pence} /></p>
              <p className="text-[11.5px] text-fg-subtle">per year · {formatPence(salary.data.hourly_rate_pence)}/hr</p>
            </div>
          )}
          {showSalaryActions && (
            <Button size="sm" variant="secondary" onClick={() => setSalaryOpen(true)}>
              New salary
            </Button>
          )}
          <Button size="sm" variant="secondary" onClick={onAddRun}><Plus /> Add run</Button>
        </div>
      </div>

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
                <RunRow key={r.id} run={r} empId={e.id}
                  onReconcile={() => setReconciling(r)}
                  onPayslip={() => setPayslipRun(r)} />
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
      {payslipRun && (
        <PayslipDialog run={payslipRun} empId={e.id}
          onOpenChange={(open) => { if (!open) setPayslipRun(null); }}
          onSaved={onChanged} />
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
function SalaryRevisionDialog({ employment, existing, onOpenChange, onSaved }: {
  employment: Employment;
  existing?: SalaryRevision;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const [date, setDate] = useState(
    existing?.effective_date ?? toISODate(new Date()));
  const [pounds, setPounds] = useState(
    existing ? String(existing.annual_salary_pence / 100) : "");
  const [notes, setNotes] = useState(existing?.notes ?? "");
  const [busy, setBusy] = useState(false);
  const valid = date && /^\d+(\.\d{1,2})?$/.test(pounds) && Number(pounds) >= 0;
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    const req = existing
      ? api.patch("/api/v1/employments/{emp_id}/salary-revisions/{rev_id}", {
          path: { emp_id: employment.id, rev_id: existing.id },
          body: {
            effective_date: date,
            annual_salary: pounds,
            notes: notes.trim() || null,
          },
        })
      : api.post("/api/v1/employments/{emp_id}/salary-revisions", {
          path: { emp_id: employment.id },
          body: {
            effective_date: date,
            annual_salary: pounds,
            notes: notes.trim() || null,
          },
        });
    req
      .then(() => {
        toast.success(`Salary updated from ${formatSmartDate(date)}`);
        onSaved();
        onOpenChange(false);
      })
      .catch((err: Error) => { toast.error(err.message); setBusy(false); });
  };
  return (
    <Dialog open onOpenChange={onOpenChange}
      title={`${existing ? "Edit" : "New"} salary — ${employment.employer}`}
      description={existing
        ? "Correct the figure or the date it took effect. Other dated entries stay as they are."
        : "The annual figure and the date it takes effect. Past salary stays on record."}
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-salaryrev"
          disabled={!valid || busy}>
          {busy ? "Saving…" : existing ? "Save changes" : "Save salary"}
        </Button>
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

function RunRow({ run: r, empId, onReconcile, onPayslip }: {
  run: Run; empId: number; onReconcile: () => void; onPayslip: () => void;
}) {
  const [open, setOpen] = useState(false);
  const detail = useApi(
    () => api.get("/api/v1/employments/{emp_id}/payroll-runs/{run_id}",
      { path: { emp_id: empId, run_id: r.id } }),
    open, [open]);
  const components = detail.data?.components ?? r.components ?? [];
  const amounts = runAmounts(r);
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
          {r.rule_set_year && r.rule_set_year !== r.tax_year && (
            <span className="ml-2 text-[11px] text-warning">using {r.rule_set_year} tax rules</span>
          )}
        </td>
        <td className="hidden px-3 text-fg-muted sm:table-cell">{formatSmartDate(r.pay_date)}</td>
        <td className="tnum px-3 text-right font-medium">
          <span className="block"><Money pence={amounts.forecastGross} /></span>
          {amounts.actualGross !== null && (
            <span className="block text-[11px] text-success"><Money pence={amounts.actualGross} /> actual</span>
          )}
        </td>
        <td className="tnum hidden px-3 text-right sm:table-cell">
          <span className="block"><Money pence={amounts.forecastNet} /></span>
          {amounts.actualNet !== null && (
            <>
              <span className="block text-[11px] text-success"><Money pence={amounts.actualNet} /> actual</span>
              <span className="block text-[11px] text-fg-muted">
                variance <Money pence={amounts.actualNet - amounts.forecastNet} signed tone="variance" />
              </span>
            </>
          )}
        </td>
        <td className="px-3"><Badge tone={RUN_TONE[r.status]}>{r.status}</Badge></td>
        <td className="pr-4 text-right">
          <Button size="sm" variant="ghost" onClick={(e) => { e.stopPropagation(); onPayslip(); }}>
            Payslip
          </Button>
          {r.status === "forecast" && (
            <>
              <Button size="sm" variant="ghost" onClick={(e) => { e.stopPropagation(); onReconcile(); }}>
                Reconcile
              </Button>
            </>
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

function PieceworkImportDialog({ employment, onOpenChange, onSaved }: {
  employment: Employment;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  type ImportRow = components["schemas"]["HistoricalPieceworkImportOut"];
  const [rows, setRows] = useState<ImportRow[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get("/api/v1/employments/{emp_id}/piecework-import/preview", {
      path: { emp_id: employment.id },
    }).then((value) => setRows(value as ImportRow[]))
      .catch((err: Error) => toast.error(err.message));
  }, [employment.id]);
  const confirm = () => {
    const ids = rows.filter((row) => row.mapped_delivery_id === null)
      .map((row) => row.transaction_id);
    if (!ids.length) return;
    setBusy(true);
    api.post("/api/v1/employments/{emp_id}/piecework-import/confirm", {
      path: { emp_id: employment.id },
      body: { transaction_ids: ids },
    }).then(() => {
      toast.success("Imported history mapped for review");
      onSaved();
      onOpenChange(false);
    }).catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };
  return (
    <Dialog open onOpenChange={onOpenChange}
      title={`Review imported history — ${employment.employer}`}
      description="Only Piecework Co imports are shown. Source transactions remain unchanged; mapped shifts are review-marked and excluded from future forecasts.">
      <div className="grid gap-3">
        {rows.length === 0 ? (
          <p className="text-[13px] text-fg-subtle">No eligible imported history found.</p>
        ) : <ul className="divide-y divide-border rounded-lg border border-border">
          {rows.map((row) => <li key={row.transaction_id}
            className="flex items-center justify-between gap-3 px-3 py-2 text-[13px]">
            <span>{formatSmartDate(row.work_date)} · <Money pence={row.imported_total_pence} /></span>
            <Badge tone={row.mapped_delivery_id === null ? "warning" : "success"}>
              {row.mapped_delivery_id === null ? "needs review" : "mapped"}
            </Badge>
          </li>)}
        </ul>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" disabled={busy || !rows.some((row) => row.mapped_delivery_id === null)}
            onClick={confirm}>{busy ? "Mapping…" : "Confirm mapping"}</Button>
        </div>
      </div>
    </Dialog>
  );
}

function PayslipDialog({ run, empId, onOpenChange, onSaved }: {
  run: Run;
  empId: number;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  type Payslip = components["schemas"]["PayslipOut"];
  const [rows, setRows] = useState<Payslip[]>([]);
  const [busy, setBusy] = useState(false);
  const [reviewing, setReviewing] = useState<Payslip | null>(null);
  const [actuals, setActuals] = useState<Record<string, string>>({});
  const basePath = `/api/v1/employments/${empId}/payroll-runs/${run.id}/payslips`;
  const load = () => api.get("/api/v1/employments/{emp_id}/payroll-runs/{run_id}/payslips", {
    path: { emp_id: empId, run_id: run.id },
  }).then((value) => setRows(value as Payslip[]))
    .catch((err: Error) => toast.error(err.message));
  useEffect(() => { load(); }, []);
  const upload = (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    api.upload(basePath, file).then((value) => {
      const row = value as Payslip;
      setRows((existing) => [...existing.filter((item) => item.id !== row.id), row]);
      onSaved();
    }).catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };
  const saveReview = () => {
    if (!reviewing) return;
    const values = Object.fromEntries(Object.entries(actuals)
      .filter(([, value]) => value.trim() !== "")
      .map(([key, value]) => [key, Math.round(Number(value) * 100)]));
    api.patch("/api/v1/employments/{emp_id}/payroll-runs/{run_id}/payslips/{payslip_id}", {
      path: { emp_id: empId, run_id: run.id, payslip_id: reviewing.id },
      body: { components: values, warnings: reviewing.warnings },
    }).then((value) => {
      setRows((existing) => existing.map((row) => row.id === reviewing.id ? value : row));
      setReviewing(value);
      toast.success("Payslip review saved");
    }).catch((err: Error) => toast.error(err.message));
  };
  const confirmReview = () => {
    if (!reviewing) return;
    const values = Object.fromEntries(Object.entries(actuals)
      .filter(([, value]) => value.trim() !== "")
      .map(([key, value]) => [key, Math.round(Number(value) * 100)]));
    if (!Object.keys(values).length) {
      toast.error("Enter at least one signed actual component");
      return;
    }
    api.post("/api/v1/employments/{emp_id}/payroll-runs/{run_id}/payslips/{payslip_id}/confirm", {
      path: { emp_id: empId, run_id: run.id, payslip_id: reviewing.id },
      body: { actuals: values },
    }).then((value) => {
      setRows((existing) => existing.map((row) => row.id === reviewing.id ? value : row));
      setReviewing(value);
      onSaved();
      toast.success("Payslip confirmed");
    }).catch((err: Error) => toast.error(err.message));
  };
  const download = (row: Payslip) => api.download(`${basePath}/${row.id}/download`)
    .then((blob) => {
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = row.original_name;
      link.click();
      URL.revokeObjectURL(link.href);
    }).catch((err: Error) => toast.error(err.message));
  return (
    <Dialog open onOpenChange={onOpenChange}
      title={`Payslips — ${formatDayMonth(run.pay_date)}`}
      description="Upload a private PDF or image. Components remain manual until you review and confirm them.">
      <div className="grid gap-4">
        <Field label="Payslip file" hint="PDF, PNG or JPEG, up to 10 MiB.">{(id) => (
          <Input id={id} type="file" accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
            disabled={busy} onChange={(event) => upload(event.target.files?.[0])} />
        )}</Field>
        {rows.length === 0 ? (
          <p className="text-[12.5px] text-fg-subtle">No payslip uploaded yet.</p>
        ) : <ul className="divide-y divide-border rounded-lg border border-border">
          {rows.map((row) => <li key={row.id} className="flex flex-wrap items-center justify-between gap-3 px-3 py-2 text-[12.5px]">
            <span className="truncate">{row.original_name}</span>
            <span className="flex items-center gap-1">
              <Badge tone={row.status === "confirmed" ? "success" : "warning"}>{row.status}</Badge>
              <Button size="sm" variant="ghost" onClick={() => {
                // Pre-fill from extraction — reviewer verifies, never
                // a silent reconcile (issue #301).
                const extracted = (row.extracted?.components ?? {}) as Record<string, number>;
                setActuals(Object.fromEntries(Object.entries(extracted)
                  .filter(([kind]) => kind in KIND_LABEL)
                  .map(([kind, pence]) => [kind, String(pence / 100)])));
                setReviewing(row);
              }}>Review</Button>
              <Button size="sm" variant="ghost" onClick={() => download(row)}>Download</Button>
            </span>
          </li>)}
        </ul>}
      </div>
      {reviewing && (
        <Dialog open onOpenChange={(open) => { if (!open) setReviewing(null); }}
          title={`Review ${reviewing.original_name}`}
          description="Enter signed actual component values. Saving review does not reconcile until you confirm.">
          <div className="grid gap-3">
            {!!reviewing.warnings?.length && (
              <ul className="rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-[12px] text-fg">
                {reviewing.warnings.map((w) => <li key={w}>{w}</li>)}
              </ul>
            )}
            {!!reviewing.overtime_findings?.length && (
              <ul className="rounded-lg border border-border px-3 py-2 text-[12px]">
                {reviewing.overtime_findings.map((f, i) => (
                  <li key={i} className="flex justify-between gap-2">
                    <span>{String(f.status)}: {String(f.printed_hours)}h @ {formatPence(Number(f.printed_rate_pence ?? 0))}</span>
                    <Badge tone="warning">{String(f.status)}</Badge>
                  </li>
                ))}
              </ul>
            )}
            {!!reviewing.extracted?.text && (
              <details className="rounded-lg border border-border px-3 py-2 text-[12px]">
                <summary className="cursor-pointer text-fg-muted">Extracted text</summary>
                <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-fg-subtle">{String(reviewing.extracted.text)}</pre>
              </details>
            )}
            {Object.keys(KIND_LABEL).map((kind) => (
              <Field key={kind} label={KIND_LABEL[kind] ?? kind}>{(id) => (
                <Input id={id} inputMode="decimal" value={actuals[kind] ?? ""}
                  onChange={(event) => setActuals((current) => ({ ...current, [kind]: event.target.value }))} />
              )}</Field>
            ))}
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={saveReview}>Save review</Button>
              <Button variant="primary" onClick={confirmReview}>Confirm actuals</Button>
            </div>
          </div>
        </Dialog>
      )}
    </Dialog>
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

function AddEmploymentDialog({ open, onOpenChange, profiles, employments, onSaved }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  profiles: Profile[];
  employments: Employment[];
  onSaved: () => void;
}) {
  const [profileId, setProfileId] = useState("");
  const [employer, setEmployer] = useState("");
  const [hours, setHours] = useState("37.5");
  const [pounds, setPounds] = useState("");
  const [taxCode, setTaxCode] = useState("");
  const [employmentType, setEmploymentType] = useState<"salaried" | "piecework">("salaried");
  const [deductionMode, setDeductionMode] = useState<"paye" | "gross_only">("paye");
  const [effDate, setEffDate] = useState(toISODate(new Date()));
  const [paydayRule, setPaydayRule] = useState("");
  const [rel, setRel] = useState<"additional" | "replacement" | null>(null);
  const [priorId, setPriorId] = useState("");
  const [endDate, setEndDate] = useState("");
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [busy, setBusy] = useState(false);
  const pid = profileId || String(profiles[0]?.id ?? "");
  const activeEmps = employments.filter(
    (e) => !e.end_date && String(e.profile_id) === pid);
  const prevEmp = activeEmps.find((e) => String(e.id) === priorId)
    ?? (activeEmps.length === 1 ? activeEmps[0] : null);
  const suggestedEnd = (() => {
    const d = new Date(effDate);
    d.setDate(d.getDate() - 1);
    return toISODate(d);
  })();
  const valid = employer.trim() && pid && Number(hours) > 0
    && (!activeEmps.length || (rel !== null
      && (rel !== "replacement" || (prevEmp !== null
        && Boolean(endDate || suggestedEnd) && confirmEnd))));
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
        payday_rule: paydayRule || null,
        start_date: effDate,
        employment_type: employmentType,
        deduction_mode: deductionMode,
        initial_salary: pounds || null,
        initial_salary_effective_date: pounds ? effDate : null,
        initial_tax_code: taxCode || null,
        replaces_employment_id: rel === "replacement" && prevEmp ? prevEmp.id : null,
        replacement_end_date: rel === "replacement" && prevEmp
          ? (endDate || suggestedEnd) : null,
        replacement_confirmed: rel === "replacement" && confirmEnd,
      },
    })
      .then(() => {
        toast.success(`Added ${employer.trim()}`);
        setEmployer("");
        setPounds("");
        setTaxCode("");
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
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Employment type">{(id) => (
            <Select id={id} value={employmentType}
              onChange={(ev) => setEmploymentType(ev.target.value as "salaried" | "piecework")}>
              <option value="salaried">Salaried</option>
              <option value="piecework">Piecework</option>
            </Select>
          )}</Field>
          <Field label="Tax deduction mode"
            hint="Gross-only is not tax exempt; record actual tax manually.">{(id) => (
            <Select id={id} value={deductionMode}
              onChange={(ev) => setDeductionMode(ev.target.value as "paye" | "gross_only")}>
              <option value="paye">PAYE</option>
              <option value="gross_only">Gross-only / cash in hand</option>
            </Select>
          )}</Field>
        </div>
        {deductionMode === "paye" && (
          <Field label="Tax code" hint="Optional until your first tax code is available.">{(id) => (
            <Input id={id} placeholder="e.g. 1257L" value={taxCode}
              onChange={(e) => setTaxCode(e.target.value.trim())} />
          )}</Field>
        )}
        <Field label="Employment starts / salary effective from"
          hint="Use the first day this employment applies.">{(id) => (
          <Input id={id} required type="date" value={effDate}
            onChange={(e) => setEffDate(e.target.value)} />
        )}</Field>
        <PaydayPicker value={paydayRule} onChange={setPaydayRule} />
        {activeEmps.length > 0 && (
          <fieldset className="grid gap-2">
            <legend className="text-[13px] font-medium">Employment relationship</legend>
            <p className="text-[12.5px] text-fg-subtle">
              Choose whether this is an additional job or replaces an existing one.
            </p>
            <label className="flex items-start gap-2 text-[13px]">
              <input type="radio" name="ff-emp-rel" className="mt-0.5"
                checked={rel === "additional"}
                onChange={() => setRel("additional")} />
              <span>
                <span className="block font-medium">Additional job</span>
                <span className="block text-[12px] text-fg-subtle">
                  Both salaries run at the same time alongside existing employment.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-[13px]">
              <input type="radio" name="ff-emp-rel" className="mt-0.5"
                checked={rel === "replacement"}
                onChange={() => { setRel("replacement"); if (!endDate) setEndDate(suggestedEnd); }} />
              <span>
                <span className="block font-medium">Replacement job</span>
                <span className="block text-[12px] text-fg-subtle">
                  Select the prior employer to end before saving.
                </span>
              </span>
            </label>
            {rel === "replacement" && (
              <>
                {activeEmps.length > 1 && (
                  <Field label="Prior employment to replace">{(id) => (
                    <Select id={id} value={priorId} onChange={(e) => {
                      setPriorId(e.target.value);
                      setEndDate("");
                      setConfirmEnd(false);
                    }}>
                      <option value="">Choose an employer</option>
                      {activeEmps.map((emp) => <option key={emp.id} value={emp.id}>{emp.employer}</option>)}
                    </Select>
                  )}</Field>
                )}
                {prevEmp && <Field label={`${prevEmp.employer} last day`}>{(id) => (
                  <Input id={id} type="date" value={endDate}
                    onChange={(e) => setEndDate(e.target.value)} />
                )}</Field>}
                {prevEmp && (
                  <>
                    <p className="rounded-lg border border-warning/25 bg-warning-soft px-3 py-2 text-[12.5px]">
                      Preview: {prevEmp.employer} will end on {formatSmartDate(endDate || suggestedEnd)}.
                    </p>
                    <label className="flex items-start gap-2 text-[12.5px]">
                      <input type="checkbox" className="mt-0.5" checked={confirmEnd}
                        onChange={(e) => setConfirmEnd(e.target.checked)} />
                      Confirm ending {prevEmp.employer} on this date.
                    </label>
                  </>
                )}
              </>
            )}
          </fieldset>
        )}
      </form>
    </Dialog>
  );
}
