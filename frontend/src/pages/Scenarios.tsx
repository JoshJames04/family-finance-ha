/** Scenarios: the what-if builder (issue #280). A scenario layers ops —
 * add a hypothetical expense/income, remove a scheduled payment, shift
 * one — onto a copy of the movement stream; "run" replays the
 * projection and diffs it against baseline: does the balance dip lower,
 * can you still afford it, does it breach payday? */
import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
  Area, AreaChart, CartesianGrid, Legend, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import { FlaskConical, Play, Plus, Trash2 } from "lucide-react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { useAccounts, useReady } from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Dialog } from "../components/Dialog";
import { Field, Input, Select } from "../components/Field";
import { PageHeader, Stat } from "../components/Display";
import { Skeleton } from "../components/Feedback";
import { Money } from "../components/Money";
import { Panel } from "../components/Panel";
import { cn } from "../lib/cn";
import {
  formatDayMonth, formatPence, formatPenceCompact, formatWeekdayDayMonth,
  toISODate,
} from "../lib/format";

type Scenario = components["schemas"]["ScenarioOut"];
type Op = components["schemas"]["ScenarioOpOut"];
type Run = components["schemas"]["ScenarioRunOut"];
type Movement = components["schemas"]["ExpectedMovementOut"];
type OpKind = "add" | "remove" | "shift";

const KIND_LABEL: Record<string, string> = {
  add: "Add", remove: "Remove", shift: "Shift",
};

export function Scenarios() {
  const ready = useReady();
  const [rev, setRev] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const scenarios = useApi(() => api.get("/api/v1/scenarios"), ready, [rev]);
  const accounts = useAccounts(rev);
  const [addingScenario, setAddingScenario] = useState(false);
  const [addingOp, setAddingOp] = useState<OpKind | null>(null);
  const refresh = () => setRev((r) => r + 1);

  const current = scenarios.data?.find((s) => s.id === selected)
    ?? scenarios.data?.[0];
  const ops = useApi(
    () => current
      ? api.get("/api/v1/scenarios/{scenario_id}/ops",
          { path: { scenario_id: current.id } })
      : Promise.resolve([]),
    ready && current != null, [current?.id, rev]);

  const movements = useApi(() => {
    const today = new Date();
    const end = new Date(today);
    end.setDate(end.getDate() + 365);
    return api.get("/api/v1/expected-movements", {
      query: { start: toISODate(today), end: toISODate(end) },
    });
  }, ready && current != null, [current?.id, rev]);

  const [accountId, setAccountId] = useState("");
  const [days, setDays] = useState("90");
  const [run, setRun] = useState<Run | null>(null);
  const [running, setRunning] = useState(false);
  const accountItems = accounts.data?.items ?? [];
  const effectiveAccount = accountId
    || (accountItems[0] ? String(accountItems[0].id) : "");

  const acctName = (id: number | null) =>
    accountItems.find((a) => a.id === id)?.name ?? "";

  const overlay = useMemo(() => {
    if (!run) return [];
    const base = new Map(run.baseline.days.map((d) => [d.date, d.balance_pence]));
    return run.projection.days.map((d) => ({
      date: d.date,
      baseline: base.get(d.date) ?? null,
      scenario: d.balance_pence,
    }));
  }, [run]);

  const doRun = () => {
    if (!current || !effectiveAccount) return;
    setRunning(true);
    api.get("/api/v1/scenarios/{scenario_id}/run", {
      path: { scenario_id: current.id },
      query: { account_id: Number(effectiveAccount), days: Number(days) || 90 },
    }).then(setRun)
      .catch((e: Error) => toast.error(e.message))
      .finally(() => setRunning(false));
  };

  const createScenario = (name: string) =>
    api.post("/api/v1/scenarios", { body: { name } })
      .then((s) => { setSelected(s.id); setAddingScenario(false); refresh(); })
      .catch((e: Error) => toast.error(e.message));

  const deleteScenario = (s: Scenario) =>
    api.delete("/api/v1/scenarios/{scenario_id}", { path: { scenario_id: s.id } })
      .then(() => { setSelected(null); setRun(null); refresh(); toast.success(`${s.name} deleted`); })
      .catch((e: Error) => toast.error(e.message));

  const deleteOp = (op: Op) =>
    api.delete("/api/v1/scenarios/{scenario_id}/ops/{op_id}", {
      path: { scenario_id: current!.id, op_id: op.id },
    }).then(() => { refresh(); setRun(null); toast.success("Change removed"); })
      .catch((e: Error) => toast.error(e.message));

  const movementLabel = (op: Op) => {
    const m = (Array.isArray(movements.data) ? movements.data : [])
      .find((x) => x.id === op.expected_movement_id);
    return m?.label ?? (op.expected_movement_id != null ? `movement #${op.expected_movement_id}` : "");
  };

  return (
    <div>
      <PageHeader
        title="Scenarios"
        description="What-if sandbox — add, remove or shift payments and see the difference before you commit."
        actions={
          <Button variant="primary" size="sm" onClick={() => setAddingScenario(true)}>
            <Plus /> New scenario
          </Button>
        }
      />

      <DataView
        state={{ ...scenarios, data: scenarios.data ?? null }}
        empty="No scenarios yet"
        emptyIcon={FlaskConical}
        emptyDescription={'Try "Cancel the gym for 3 months" or "What if the boiler needs £400?" — build the change, run it, see the balance curve.'}
        emptyAction={<Button variant="primary" onClick={() => setAddingScenario(true)}><Plus /> New scenario</Button>}
        loading={<Skeleton className="h-64 w-full rounded-xl" />}
      >
        {(list) => (
          <div className="grid gap-4 lg:grid-cols-[minmax(0,260px)_1fr]">
            {/* Scenario picker */}
            <Panel title="Scenarios" flush>
              <ul className="divide-y divide-border">
                {list.map((s) => (
                  <li key={s.id}>
                    <button
                      type="button"
                      onClick={() => { setSelected(s.id); setRun(null); }}
                      className={cn("flex w-full items-center justify-between gap-2 px-4 py-2.5 text-left text-[13.5px] transition-colors hover:bg-surface-2/60",
                        current?.id === s.id && "bg-accent-soft/50 font-medium")}
                      aria-current={current?.id === s.id}
                    >
                      <span className="truncate">{s.name}</span>
                      <Trash2
                        role="button"
                        aria-label={`Delete ${s.name}`}
                        className="size-3.5 shrink-0 text-fg-subtle hover:text-negative"
                        onClick={(e) => { e.stopPropagation(); void deleteScenario(s); }}
                      />
                    </button>
                  </li>
                ))}
              </ul>
            </Panel>

            {current && (
              <div className="space-y-4">
                {/* Ops layer */}
                <Panel
                  title={`Changes — ${current.name}`}
                  description="Each change applies on top of the real schedule — the ledger itself is never touched."
                  flush
                  actions={
                    <div className="flex gap-1.5">
                      {(["add", "remove", "shift"] as const).map((k) => (
                        <Button key={k} variant="secondary" size="sm"
                          onClick={() => setAddingOp(k)}
                          aria-label={`${KIND_LABEL[k]} a change`}>
                          {KIND_LABEL[k]}
                        </Button>
                      ))}
                    </div>
                  }
                >
                  {(ops.data ?? []).length === 0 ? (
                    <p className="px-4 py-6 text-center text-[13px] text-fg-subtle">
                      No changes yet — add one above.
                    </p>
                  ) : (
                    <ul className="divide-y divide-border">
                      {(ops.data ?? []).map((o) => (
                        <li key={o.id} className="flex items-center gap-3 px-4 py-2.5">
                          <Badge tone={o.kind === "add" ? "success" : o.kind === "remove" ? "error" : "info"}>
                            {KIND_LABEL[o.kind] ?? o.kind}
                          </Badge>
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-[13.5px]">
                              {o.kind === "add"
                                ? <>{o.label ?? "Custom"} · {acctName(o.account_id)} · {formatDayMonth(o.expected_date ?? "")}</>
                                : <>{movementLabel(o)}{o.kind === "shift" && o.expected_date ? ` → ${formatDayMonth(o.expected_date)}` : ""}</>}
                            </p>
                          </div>
                          {o.amount_pence != null && (
                            <Money pence={o.amount_pence} signed tone="auto" className="font-medium" />
                          )}
                          <Button variant="ghost" size="icon-sm"
                            aria-label={`Remove change ${o.label ?? o.id}`}
                            onClick={() => deleteOp(o)}>
                            <Trash2 />
                          </Button>
                        </li>
                      ))}
                    </ul>
                  )}
                </Panel>

                {/* Run controls + result */}
                <Panel
                  title="Run the what-if"
                  description="Replays the balance projection with your changes applied."
                >
                  <div className="flex flex-wrap items-end gap-3">
                    <Field label="Account" className="min-w-44 flex-1">{(_id) => (
                      <Select aria-label="Scenario account" value={effectiveAccount}
                        onChange={(e) => { setAccountId(e.target.value); setRun(null); }}>
                        {accountItems.map((a) => (
                          <option key={a.id} value={a.id}>{a.name}</option>
                        ))}
                      </Select>
                    )}</Field>
                    <Field label="Horizon">{(_id) => (
                      <Select aria-label="Horizon" value={days} onChange={(e) => setDays(e.target.value)}>
                        <option value="30">30 days</option>
                        <option value="90">90 days</option>
                        <option value="180">180 days</option>
                        <option value="365">1 year</option>
                      </Select>
                    )}</Field>
                    <Button variant="primary" onClick={doRun} disabled={running || !effectiveAccount}>
                      <Play /> {running ? "Running…" : "Run"}
                    </Button>
                  </div>

                  {run && (
                    <div className="mt-4 space-y-4">
                      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
                        <Stat label="Lowest point change"
                          value={<Money pence={run.diff.min_balance_delta_pence} signed tone="auto" />}
                          hint={run.diff.baseline_min_balance_date
                            ? `${formatDayMonth(run.diff.baseline_min_balance_date)} → ${formatDayMonth(run.diff.scenario_min_balance_date ?? "")}`
                            : "no change"}
                          className="rounded-lg border border-border bg-surface-2/60 p-3" />
                        <Stat label="Safe to spend change"
                          value={<Money pence={run.diff.safe_to_spend_delta_pence} signed tone="auto" />}
                          className="rounded-lg border border-border bg-surface-2/60 p-3" />
                        <Stat label="Scenario lowest"
                          value={<Money pence={run.diff.scenario_min_balance_pence} />}
                          hint={`was ${formatPence(run.diff.baseline_min_balance_pence)}`}
                          className="hidden rounded-lg border border-border bg-surface-2/60 p-3 lg:block" />
                      </div>
                      <ScenarioChart data={overlay} />
                      {run.diff.paydays.length > 0 && (
                        <div>
                          <p className="mb-1.5 text-[12.5px] font-medium text-fg-muted">Balance on paydays</p>
                          <ul className="divide-y divide-border rounded-lg border border-border">
                            {run.diff.paydays.map((p) => (
                              <li key={p.date} className="flex items-center justify-between px-3 py-2 text-[13px]">
                                <span>{formatWeekdayDayMonth(p.date)}</span>
                                <span className="tnum">
                                  <Money pence={p.baseline_balance_pence} />
                                  <span className="mx-1.5 text-fg-subtle">→</span>
                                  <Money pence={p.scenario_balance_pence} />
                                  <Money pence={p.delta_pence} signed tone="auto" className="ml-2" />
                                </span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  )}
                </Panel>
              </div>
            )}
          </div>
        )}
      </DataView>

      {addingScenario && (
        <ScenarioDialog onClose={() => setAddingScenario(false)} onSave={createScenario} />
      )}
      {addingOp && current && (
        <OpDialog
          kind={addingOp}
          scenarioId={current.id}
          accounts={accountItems}
          movements={Array.isArray(movements.data) ? movements.data : []}
          onClose={() => setAddingOp(null)}
          onSaved={() => { setAddingOp(null); setRun(null); refresh(); }}
        />
      )}
    </div>
  );
}

/** Baseline vs scenario overlay — same balance-curve language as the
 * Forecast chart, second series dashed. */
function ScenarioChart({ data }: {
  data: { date: string; baseline: number | null; scenario: number }[];
}) {
  if (data.length < 2) return null;
  return (
    <div role="img" aria-label="Scenario projection compared with baseline"
      className="h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeDasharray="3 4" />
          <XAxis dataKey="date" tickFormatter={formatDayMonth} tickLine={false}
            axisLine={false} minTickGap={48}
            tick={{ fill: "var(--fg-subtle)", fontSize: 11.5 }} dy={6} />
          <YAxis tickFormatter={formatPenceCompact} tickLine={false} axisLine={false}
            width={56} tick={{ fill: "var(--fg-subtle)", fontSize: 11.5 }} />
          <Tooltip
            formatter={(v) => formatPence(Number(v))}
            labelFormatter={(l) => formatWeekdayDayMonth(String(l))}
            contentStyle={{
              background: "var(--surface)", border: "1px solid var(--border)",
              borderRadius: 10, fontSize: 12.5,
            }}
          />
          <Legend wrapperStyle={{ fontSize: 12.5 }} />
          <Area type="monotone" dataKey="baseline" name="Baseline"
            stroke="var(--fg-subtle)" strokeWidth={1.5} strokeDasharray="5 4"
            fill="none" isAnimationActive={false} />
          <Area type="monotone" dataKey="scenario" name="Scenario"
            stroke="var(--chart-line)" strokeWidth={2} fill="var(--chart-line)"
            fillOpacity={0.12} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function ScenarioDialog({ onClose, onSave }: {
  onClose: () => void;
  onSave: (name: string) => void;
}) {
  const [name, setName] = useState("");
  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}
      title="New scenario"
      description='A name for the what-if — "No gym for 3 months", "Car insurance hikes £20/mo".'
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-scenario" disabled={!name.trim()}>
          Create
        </Button>
      </>}>
      <form id="ff-scenario" onSubmit={(e) => { e.preventDefault(); if (name.trim()) onSave(name.trim()); }}>
        <Field label="Name">{(_id) => (
          <Input aria-label="Scenario name" required autoFocus
            value={name} onChange={(e) => setName(e.target.value)} />
        )}</Field>
      </form>
    </Dialog>
  );
}

function OpDialog({ kind, scenarioId, accounts, movements, onClose, onSaved }: {
  kind: OpKind;
  scenarioId: number;
  accounts: components["schemas"]["AccountOut"][];
  movements: Movement[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [label, setLabel] = useState("");
  const [pounds, setPounds] = useState("");
  const [date, setDate] = useState(toISODate(new Date()));
  const [accountId, setAccountId] = useState(accounts[0] ? String(accounts[0].id) : "");
  const [movementId, setMovementId] = useState("");
  const [busy, setBusy] = useState(false);

  const picked = movements.find((m) => String(m.id) === movementId);
  const canSubmit = kind === "add"
    ? label.trim() && pounds && accountId && date
    : movementId && (kind === "shift" ? date : true);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    api.post("/api/v1/scenarios/{scenario_id}/ops", {
      path: { scenario_id: scenarioId },
      body: {
        kind,
        ...(kind === "add" ? {
          account_id: Number(accountId),
          expected_date: date,
          amount: Math.round(Number(pounds) * 100),
          label: label.trim(),
        } : {
          expected_movement_id: Number(movementId),
          ...(kind === "shift" ? { expected_date: date } : {}),
        }),
      },
    }).then(() => { toast.success("Change added"); onSaved(); })
      .catch((err: Error) => { toast.error(err.message); setBusy(false); });
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}
      title={`${KIND_LABEL[kind]} a change`}
      description={kind === "add"
        ? "A hypothetical payment or income — negative £400 spends, positive adds."
        : kind === "remove"
          ? "Suppress a scheduled payment — the real one stays, the projection skips it."
          : "Move a scheduled payment to a different date."}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-op" disabled={busy || !canSubmit}>
          {busy ? "Adding…" : "Add change"}
        </Button>
      </>}>
      <form id="ff-op" onSubmit={submit} className="grid gap-4">
        {kind === "add" ? (
          <>
            <Field label="Label">{(_id) => (
              <Input aria-label="Op label" required autoFocus
                value={label} onChange={(e) => setLabel(e.target.value)}
                placeholder="e.g. New boiler" />
            )}</Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Amount (£)" hint="Negative spends, positive adds.">{(_id) => (
                <Input aria-label="Op amount" required type="number" step="0.01"
                  value={pounds} onChange={(e) => setPounds(e.target.value)} />
              )}</Field>
              <Field label="On">{(_id) => (
                <Input aria-label="Op date" required type="date"
                  value={date} onChange={(e) => setDate(e.target.value)} />
              )}</Field>
            </div>
            <Field label="Account">{(_id) => (
              <Select aria-label="Op account" value={accountId}
                onChange={(e) => setAccountId(e.target.value)}>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </Select>
            )}</Field>
          </>
        ) : (
          <>
            <Field label="Scheduled payment">{(_id) => (
              <Select aria-label="Movement" required value={movementId}
                onChange={(e) => setMovementId(e.target.value)}>
                <option value="" disabled>Choose…</option>
                {movements.map((m) => (
                  <option key={m.id} value={m.id}>
                    {formatDayMonth(m.expected_date)} — {m.label} — {formatPence(m.amount_pence)}
                  </option>
                ))}
              </Select>
            )}</Field>
            {kind === "shift" && (
              <Field label="Move to">{(_id) => (
                <Input aria-label="New date" required type="date"
                  value={date} onChange={(e) => setDate(e.target.value)} />
              )}</Field>
            )}
            {kind === "remove" && picked && (
              <p className="text-[12.5px] text-fg-subtle">
                Suppresses <Money pence={picked.amount_pence} signed /> expected on {formatDayMonth(picked.expected_date)} — the real schedule is untouched.
              </p>
            )}
          </>
        )}
      </form>
    </Dialog>
  );
}
