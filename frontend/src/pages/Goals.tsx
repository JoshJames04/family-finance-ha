/** Savings goals: target tracking with contributed-vs-target progress,
 * projected completion and the monthly amount needed to stay on track. */
import { useState } from "react";
import { toast } from "sonner";
import { PiggyBank, Plus } from "lucide-react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { useAccounts, useReady } from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Panel } from "../components/Panel";
import { Badge } from "../components/Badge";
import { Money } from "../components/Money";
import { Button } from "../components/Button";
import { Dialog } from "../components/Dialog";
import { Field, Input, Select } from "../components/Field";
import { IconTile, PageHeader, Progress, Ring } from "../components/Display";
import { Skeleton } from "../components/Feedback";
import { formatDayMonthYear, formatPence, toISODate } from "../lib/format";

type Goal = components["schemas"]["SavingsGoalOut"];
type ProgressOut = components["schemas"]["GoalProgressOut"];
type GoalType = components["schemas"]["GoalType"];

const TYPE_LABEL: Record<GoalType, string> = {
  emergency: "Emergency fund", holiday: "Holiday", christmas: "Christmas",
  annual_reserve: "Annual reserve", general: "General",
};
const TYPES = Object.keys(TYPE_LABEL) as GoalType[];

export function Goals() {
  const ready = useReady();
  const [rev, setRev] = useState(0);
  const [adding, setAdding] = useState(false);
  const [showInactive, setShowInactive] = useState(false);
  const goals = useApi(
    () => api.get("/api/v1/savings-goals", { query: { include_inactive: true } }),
    ready, [rev]);
  const items = (goals.data?.items ?? []).filter((g) => showInactive || g.active);
  const inactive = (goals.data?.items ?? []).filter((g) => !g.active).length;
  const refresh = () => setRev((r) => r + 1);

  const close = (g: Goal) =>
    api.delete("/api/v1/savings-goals/{gid}", { path: { gid: g.id } })
      .then(() => { toast.success(`${g.name} closed`); refresh(); })
      .catch((e: Error) => toast.error(e.message));

  return (
    <div>
      <PageHeader
        title="Goals"
        description="Savings targets and whether you're putting away enough to hit them."
        actions={
          <>
            {inactive > 0 && (
              <Button variant="ghost" onClick={() => setShowInactive((s) => !s)}>
                {showInactive ? "Hide closed" : `Show closed (${inactive})`}
              </Button>
            )}
            <Button variant="primary" onClick={() => setAdding(true)}><Plus /> New goal</Button>
          </>
        }
      />

      <DataView
        state={{ ...goals, data: items }}
        empty="No savings goals yet"
        emptyIcon={PiggyBank}
        emptyDescription="Set a target — emergency fund, holiday, Christmas — and track contributions toward it."
        emptyAction={<Button variant="primary" onClick={() => setAdding(true)}><Plus /> New goal</Button>}
        loading={<div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-48 rounded-xl" />)}</div>}
      >
        {(list) => (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {list.map((g) => <GoalCard key={g.id} goal={g} rev={rev} onClose={() => close(g)} />)}
          </div>
        )}
      </DataView>

      <AddGoalDialog open={adding} onOpenChange={setAdding} onSaved={refresh} />
    </div>
  );
}

function GoalCard({ goal: g, rev, onClose }: { goal: Goal; rev: number; onClose: () => void }) {
  const progress = useApi(
    () => api.get("/api/v1/savings-goals/{gid}/progress", { path: { gid: g.id } }),
    true, [g.id, rev]);
  const p: ProgressOut | null = progress.data;
  const pct = p ? p.pct : 0;
  const done = pct >= 100;
  return (
    <Panel className={g.active ? undefined : "opacity-60"}>
      <div id={`savings_goal-${g.id}`} className="flex items-start gap-3">
        <IconTile icon={PiggyBank} label={g.name} size="lg" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[15px] font-semibold">{g.name}</p>
          <p className="text-[12.5px] text-fg-subtle">
            {TYPE_LABEL[g.goal_type]}
            {g.target_date ? ` · by ${formatDayMonthYear(g.target_date)}` : ""}
            {!g.active ? " · closed" : ""}
          </p>
        </div>
        {done && <Badge tone="success">Funded</Badge>}
        {!done && p?.on_track === true && <Badge tone="info">On track</Badge>}
        {!done && p?.on_track === false && <Badge tone="warning">Behind</Badge>}
      </div>

      <div className="mt-4 flex items-center gap-4">
        <Ring ratio={pct / 100} size={84} stroke={9}>
          <span className="tnum text-[15px] font-semibold">{pct}%</span>
        </Ring>
        <div className="min-w-0 flex-1 space-y-1 text-[13px]">
          {progress.loading ? (
            <><Skeleton className="h-4 w-3/4" /><Skeleton className="h-4 w-1/2" /></>
          ) : p ? (
            <>
              <p><Money pence={p.contributed_pence} className="font-semibold" /> <span className="text-fg-subtle">of {formatPence(g.target_pence)}</span></p>
              <p className="text-fg-subtle"><Money pence={p.remaining_pence} /> to go{p.monthly_need_pence !== null ? ` · ${formatPence(p.monthly_need_pence)}/mo needed` : ""}</p>
              {p.projected_date && !done && (
                <p className="text-fg-subtle">Projected {formatDayMonthYear(p.projected_date)}</p>
              )}
            </>
          ) : <p className="text-fg-subtle">—</p>}
        </div>
      </div>

      <div className="mt-4 border-t border-border pt-3">
        <Progress value={p?.contributed_pence ?? 0} max={g.target_pence} tone="positive" label={`${g.name} funded`} />
        {g.active && (
          <div className="mt-3 flex justify-end">
            <Button size="sm" variant="ghost" onClick={onClose} aria-label={`Close ${g.name}`}>Close goal</Button>
          </div>
        )}
      </div>
    </Panel>
  );
}

function AddGoalDialog({ open, onOpenChange, onSaved }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const accounts = useAccounts();
  const [name, setName] = useState("");
  const [type, setType] = useState<GoalType>("general");
  const [target, setTarget] = useState("");
  const [targetDate, setTargetDate] = useState("");
  const [accountId, setAccountId] = useState("");
  const [busy, setBusy] = useState(false);
  const acct = accountId || String(accounts.data?.items[0]?.id ?? "");
  const valid = name.trim() && acct && /^\d+(\.\d{1,2})?$/.test(target);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    api.post("/api/v1/savings-goals", {
      body: {
        name: name.trim(),
        goal_type: type,
        target,
        target_date: targetDate || null,
        start_date: toISODate(new Date()),
        account_id: Number(acct),
      },
    })
      .then(() => {
        toast.success(`Goal “${name.trim()}” created`);
        setName(""); setTarget(""); setTargetDate("");
        onSaved();
        onOpenChange(false);
      })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="New savings goal"
      description="Set a target and the app tracks contributions from the linked account."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-add-goal" disabled={!valid || busy}>{busy ? "Creating…" : "Create goal"}</Button>
      </>}>
      <form id="ff-add-goal" onSubmit={submit} className="grid gap-4">
        <Field label="Name">{(id) => <Input id={id} autoFocus placeholder="e.g. Summer holiday" value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Type">{(id) => (
            <Select id={id} value={type} onChange={(e) => setType(e.target.value as GoalType)}>
              {TYPES.map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
            </Select>
          )}</Field>
          <Field label="Saved in">{(id) => (
            <Select id={id} value={acct} onChange={(e) => setAccountId(e.target.value)}>
              {(accounts.data?.items ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select>
          )}</Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Target (£)">{(id) => <Input id={id} inputMode="decimal" placeholder="0.00" value={target} onChange={(e) => setTarget(e.target.value.trim())} />}</Field>
          <Field label="Target date" hint="Optional — enables the monthly-need figure.">{(id) => <Input id={id} type="date" value={targetDate} onChange={(e) => setTargetDate(e.target.value)} />}</Field>
        </div>
      </form>
    </Dialog>
  );
}
