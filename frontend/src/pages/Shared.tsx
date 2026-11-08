/** Shared: the household bills pot (issue #277). What the shared
 * accounts need this period, how much each member has allocated via
 * their contribution rules, what's actually been paid in, and what's
 * still outstanding — plus the rule management that sets each member's
 * share. Contribution matching links real transfer transactions to the
 * expected amounts. */
import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
  ArrowLeftRight, CalendarClock, Check, HandCoins, Pencil, Plus,
  Scale, Users2,
} from "lucide-react";
import { api, getProfileId } from "../api/client";
import { useApi, useIdentity } from "../api/useApi";
import { useAccounts, useReady } from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Badge, type BadgeTone } from "../components/Badge";
import { Button } from "../components/Button";
import { Dialog } from "../components/Dialog";
import { Field, Input, Select } from "../components/Field";
import { IconTile, PageHeader, Progress, Stat } from "../components/Display";
import { EmptyState, Skeleton } from "../components/Feedback";
import { Money } from "../components/Money";
import { Panel } from "../components/Panel";
import { cn } from "../lib/cn";
import {
  formatDayMonth, formatMonthOf, formatPence, formatSmartDate, toISODate,
} from "../lib/format";
import { toneFor } from "../lib/visuals";

type Profile = components["schemas"]["ProfileOut"];
type Movement = components["schemas"]["ExpectedMovementOut"];
type Contribution = components["schemas"]["ContributionStatusOut"];
type Requirement = components["schemas"]["RequirementOut"];
type Outstanding = components["schemas"]["OutstandingItemOut"];
type Rule = components["schemas"]["TransferRuleOut"];
type Txn = components["schemas"]["TransactionOut"];
type Account = components["schemas"]["AccountOut"];
type Frequency = components["schemas"]["Frequency"];

const STATE_TONE: Record<string, BadgeTone> = {
  satisfied: "success", partial: "warning", overdue: "error",
  outstanding: "info", scheduled: "info", none: "neutral",
  unmatched: "neutral",
};

function monthRange(): { start: string; end: string; label: string } {
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), 1);
  const end = new Date(now.getFullYear(), now.getMonth() + 1, 0);
  return {
    start: toISODate(start), end: toISODate(end),
    label: formatMonthOf(toISODate(start)),
  };
}

/** Parse a rule's amount spec into a readable summary. */
function specLabel(spec: Rule["amount_spec"]): string {
  const k = spec.kind as string;
  if (k === "fixed") return "Fixed amount";
  if (k === "income_pct") return `${(Number(spec.bps ?? 0) / 100).toString()}% of income`;
  if (k === "budget_line") return "From budget line";
  return k;
}

export function Shared() {
  const ready = useReady();
  const identity = useIdentity();
  const me = identity.data ? getProfileId() : null;
  const [rev, setRev] = useState(0);
  const period = useMemo(monthRange, []);
  const accounts = useAccounts(rev);
  const rules = useApi(() => api.get("/api/v1/transfer-rules"), ready, [rev]);
  const movements = useApi(
    () => api.get("/api/v1/expected-movements", { query: { start: period.start, end: period.end } }),
    ready, [period.start, period.end, rev]);
  const contributions = useApi(
    () => api.get("/api/v1/expected-movements/contributions", { query: { start: period.start, end: period.end } }),
    ready, [period.start, period.end, rev]);
  const byMember = useApi(
    () => api.get("/api/v1/expected-movements/contributions/by-member", { query: { start: period.start, end: period.end } }),
    ready, [period.start, period.end, rev]);
  const requirements = useApi(
    () => api.get("/api/v1/expected-movements/requirements", { query: { start: period.start, end: period.end } }),
    ready, [period.start, period.end, rev]);
  const outstanding = useApi(
    () => api.get("/api/v1/expected-movements/outstanding"), ready, [rev]);
  const [editing, setEditing] = useState<Rule | "new" | null>(null);
  const [matching, setMatching] = useState<{ movement: Movement; member: string } | null>(null);
  const refresh = () => setRev((r) => r + 1);

  const acctById = useMemo(() => {
    const m = new Map<number, Account>();
    for (const a of accounts.data?.items ?? []) m.set(a.id, a);
    return m;
  }, [accounts.data]);

  const ruleItems = useMemo(
    () => (rules.data?.items ?? []).filter((r) => r.active),
    [rules.data]);
  const inactiveRules = useMemo(
    () => (rules.data?.items ?? []).filter((r) => !r.active),
    [rules.data]);
  const ruleById = useMemo(() => {
    const m = new Map<number, Rule>();
    for (const r of rules.data?.items ?? []) m.set(r.id, r);
    return m;
  }, [rules.data]);

  /** The member a transfer rule belongs to — its source account owner. */
  const ruleOwner = (r: Rule) => acctById.get(r.source_account_id)?.owner_profile_id ?? null;
  /** Shared (pot) accounts: unowned accounts that rules pay into. */
  const sharedAccountIds = useMemo(() => {
    const ids = new Set<number>();
    for (const r of ruleItems) {
      if ((acctById.get(r.dest_account_id)?.owner_profile_id ?? null) === null) {
        ids.add(r.dest_account_id);
      }
    }
    return ids;
  }, [ruleItems, acctById]);

  /* ---- member standing: expected from rule "in" legs, matched from
   * contribution status ---- */
  const statusByMovement = useMemo(() => {
    const m = new Map<number, Contribution>();
    for (const c of (Array.isArray(contributions.data) ? contributions.data : [])) {
      m.set(c.movement_id, c);
    }
    return m;
  }, [contributions.data]);

  type MemberStanding = {
    profile: Profile;
    expected: number;
    matched: number;
    outstanding: number;
    rules: Rule[];
    state: string;
    unpaid: Movement[];
  };
  const standings = useMemo<MemberStanding[]>(() => {
    const mvts = (Array.isArray(movements.data) ? movements.data : [])
      .filter((m) =>
        m.source_type === "transfer_rule" &&
        m.amount_pence > 0 &&
        m.account_id != null && sharedAccountIds.has(m.account_id));
    const paid = new Map<number, number>();
    for (const c of (Array.isArray(byMember.data) ? byMember.data : [])) {
      paid.set(c.profile_id, c.contributed_pence);
    }
    const members = (identity.data ?? []).filter((p) => p.is_active);
    return members.map((p) => {
      const mine = mvts.filter((m) => {
        const rule = ruleById.get(m.source_id);
        return rule ? ruleOwner(rule) === p.id : false;
      });
      const expected = mine.reduce((s, m) => s + m.amount_pence, 0);
      const matched = mine.reduce(
        (s, m) => s + (statusByMovement.get(m.id)?.matched_pence ?? 0), 0);
      // Paid-in transfers may exceed scheduled expectations (early/extra).
      const effective = Math.max(matched, paid.get(p.id) ?? 0);
      const outstanding = Math.max(0, expected - effective);
      const memberRules = ruleItems.filter((r) => ruleOwner(r) === p.id);
      const state = memberRules.length === 0 ? "none"
        : expected === 0 ? "scheduled"
        : outstanding === 0 ? "satisfied"
        : effective > 0 ? "partial" : "outstanding";
      return {
        profile: p, expected, matched: effective, outstanding,
        rules: memberRules,
        state,
        unpaid: mine.filter((m) =>
          (statusByMovement.get(m.id)?.outstanding_pence ?? m.amount_pence) > 0),
      };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [movements.data, contributions.data, byMember.data, identity.data,
      ruleById, ruleItems, sharedAccountIds, acctById]);

  const totalDue = standings.reduce((s, m) => s + m.expected, 0);
  const totalPaid = standings.reduce((s, m) => s + Math.min(m.matched, m.expected), 0);
  const totalOutstanding = standings.reduce((s, m) => s + m.outstanding, 0);
  const overdue = (Array.isArray(outstanding.data) ? outstanding.data : [])
    .filter((o) => o.account_id == null || sharedAccountIds.has(o.account_id));

  const autoMatch = () =>
    api.post("/api/v1/expected-movements/match")
      .then((r) => {
        toast.success(r.matched > 0 ? `Matched ${r.matched} payment${r.matched === 1 ? "" : "s"}` : "No new matches found");
        refresh();
      }).catch((e: Error) => toast.error(e.message));

  const toggleRule = (r: Rule) =>
    api.delete("/api/v1/transfer-rules/{rid}", { path: { rid: r.id } })
      .then(() => { toast.success(`${r.name} paused`); refresh(); })
      .catch((e: Error) => toast.error(e.message));

  return (
    <div>
      <PageHeader
        title="Shared"
        description="The household bills pot — who pays what, and whether it's in."
        actions={
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" onClick={autoMatch}>
              <ArrowLeftRight /> Match payments
            </Button>
            <Button variant="primary" size="sm" onClick={() => setEditing("new")}>
              <Plus /> Set a share
            </Button>
          </div>
        }
      />

      <div className="mb-4 flex items-center gap-2 text-[12.5px] text-fg-subtle">
        <CalendarClock className="size-3.5" /> {period.label}
      </div>

      <DataView
        state={{
          loading: rules.loading || movements.loading || contributions.loading,
          error: rules.error ?? movements.error ?? contributions.error,
          data: "ready",
        }}
        empty=""
        loading={<Skeleton className="h-64 w-full rounded-xl" />}
      >
        {() => (
          <>
            <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat label="Due this month" value={<Money pence={totalDue} />}
                hint={`${ruleItems.length} share${ruleItems.length === 1 ? "" : "s"} set`}
                className="rounded-xl border border-border bg-surface p-4 shadow-card" />
              <Stat label="Paid in" value={<Money pence={totalPaid} />}
                hint={totalDue > 0 ? `${Math.round(totalPaid / totalDue * 100)}% covered` : undefined}
                className="rounded-xl border border-border bg-surface p-4 shadow-card" />
              <Stat label="Still to pay" value={<Money pence={totalOutstanding} />}
                className={cn("rounded-xl border border-border bg-surface p-4 shadow-card",
                  totalOutstanding > 0 && "border-warning/40")} />
              <Stat label="Overdue items" value={overdue.length}
                className="rounded-xl border border-border bg-surface p-4 shadow-card" />
            </div>

            {/* Member standing */}
            <div className="mb-4 grid gap-3 lg:grid-cols-2">
              {standings.map((m) => (
                <Panel key={m.profile.id}
                  className={cn(m.profile.id === me && "ring-1 ring-accent/40")}>
                  <div className="mb-3 flex items-start gap-3">
                    <span className={cn("grid size-10 shrink-0 place-items-center rounded-full text-[14px] font-semibold",
                      toneFor(m.profile.display_name).split(" ").slice(0, 2).join(" "))}>
                      {m.profile.display_name.slice(0, 1).toUpperCase()}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-[15px] font-semibold">
                        {m.profile.display_name}
                        {m.profile.id === me && <span className="ml-1.5 text-[12px] font-normal text-fg-subtle">(you)</span>}
                      </p>
                      <p className="text-[12px] text-fg-subtle">
                        {m.rules.length === 0 ? "No share set"
                          : m.rules.map((r) => specLabel(r.amount_spec) === "Fixed amount"
                              ? <span key={r.id}><Money pence={Number((r.amount_spec as { pence?: number }).pence ?? 0)} /> on day {r.day_of_month}</span>
                              : <span key={r.id}>{specLabel(r.amount_spec)}</span>)}
                      </p>
                    </div>
                    <Badge tone={STATE_TONE[m.state] ?? "neutral"}>
                      {m.state === "none" ? "no share" : m.state === "scheduled" ? "scheduled" : m.state}
                    </Badge>
                  </div>
                  {m.expected > 0 ? (
                    <>
                      <div className="mb-2 flex items-baseline justify-between text-[13px]">
                        <span className="text-fg-muted">
                          <Money pence={m.matched} /> of <Money pence={m.expected} />
                        </span>
                        {m.outstanding > 0 && (
                          <span className="text-warning"><Money pence={m.outstanding} /> to go</span>
                        )}
                      </div>
                      <Progress value={m.matched} max={m.expected}
                        label={`${m.profile.display_name} contribution`} />
                      {m.unpaid.length > 0 && (
                        <div className="mt-2.5 flex justify-end">
                          <Button size="sm" variant="secondary"
                            onClick={() => setMatching({
                              movement: m.unpaid[0]!,
                              member: m.profile.display_name,
                            })}
                            aria-label={`Record ${m.profile.display_name}'s payment`}>
                            <Check /> Record payment
                          </Button>
                        </div>
                      )}
                    </>
                  ) : (
                    <p className="text-[12.5px] text-fg-subtle">
                      {m.rules.length > 0
                        ? `Nothing due this month — next payment is scheduled by ${m.profile.display_name}'s rule.`
                        : `Set ${m.profile.display_name}'s share with a contribution rule below.`}
                    </p>
                  )}
                </Panel>
              ))}
            </div>

            {/* Requirement per shared account — the API emits one row
             * per movement leg, so aggregate to (account, due date). */}
            {(() => {
              const agg = new Map<string, Requirement>();
              for (const r of (Array.isArray(requirements.data) ? requirements.data : [])) {
                if (!sharedAccountIds.has(r.account_id)) continue;
                const k = `${r.account_id}-${r.due_date}`;
                const cur = agg.get(k);
                agg.set(k, cur
                  ? { ...cur, scheduled_pence: cur.scheduled_pence + r.scheduled_pence,
                      satisfied_pence: cur.satisfied_pence + r.satisfied_pence,
                      outstanding_pence: cur.outstanding_pence + r.outstanding_pence }
                  : { ...r });
              }
              const reqs = [...agg.values()];
              return reqs.length > 0 && (
              <Panel title="The pot needs" flush
                description="Expected payments into shared accounts minus what's already covered."
                className="mb-4">
                <ul className="divide-y divide-border">
                  {reqs.map((r) => (
                      <li key={`${r.account_id}-${r.due_date}`}
                        className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3">
                        <IconTile label={acctById.get(r.account_id)?.name ?? "Account"} />
                        <div className="min-w-0 flex-1">
                          <p className="text-[13.5px] font-medium">
                            {acctById.get(r.account_id)?.name ?? `Account ${r.account_id}`}
                          </p>
                          <p className="text-[12px] text-fg-subtle">by {formatDayMonth(r.due_date)}</p>
                        </div>
                        <div className="text-right text-[13px]">
                          <Money pence={r.outstanding_pence} className="font-semibold" />
                          <p className="text-[11.5px] text-fg-subtle">
                            of <Money pence={r.scheduled_pence} /> due
                          </p>
                        </div>
                      </li>
                    ))}
                </ul>
              </Panel>
              );
            })()}

            {/* Contribution rules */}
            <Panel
              title="Contribution shares"
              description="Each member's standing order into the pot — edit the amount or the day any time."
              flush
              className="mb-4"
            >
              {ruleItems.length === 0 && inactiveRules.length === 0 ? (
                <EmptyState compact icon={Scale} title="No shares set"
                  description="Set each member's monthly share — rules generate the expected payments this page tracks." />
              ) : (
                <ul className="divide-y divide-border">
                  {[...ruleItems, ...inactiveRules].map((r) => {
                    const owner = ruleOwner(r);
                    const ownerName = identity.data?.find((p) => p.id === owner)?.display_name
                      ?? (owner == null ? "Shared" : `Member ${owner}`);
                    const src = acctById.get(r.source_account_id);
                    const dst = acctById.get(r.dest_account_id);
                    return (
                      <li key={r.id} className={cn("flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3", !r.active && "opacity-60")}>
                        <IconTile icon={HandCoins} label={r.name} />
                        <div className="min-w-0 flex-1">
                          <p className="text-[13.5px] font-medium">{r.name}</p>
                          <p className="text-[12px] text-fg-subtle">
                            {ownerName} · {src?.name ?? "?"} → {dst?.name ?? "?"} ·
                            {" "}{r.frequency} · day {r.day_of_month}
                          </p>
                        </div>
                        {r.amount_spec.kind === "fixed"
                          ? <Money pence={Number((r.amount_spec as { pence?: number }).pence ?? 0)} className="font-medium" />
                          : <Badge tone="info">{specLabel(r.amount_spec)}</Badge>}
                        <div className="flex gap-1">
                          <Button size="sm" variant="ghost" onClick={() => setEditing(r)}
                            aria-label={`Edit ${r.name}`}><Pencil /></Button>
                          <Button size="sm" variant="ghost" onClick={() => toggleRule(r)}
                            aria-label={`Pause ${r.name}`}>{r.active ? "Pause" : "Paused"}</Button>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </Panel>

            {/* Overdue obligations */}
            {overdue.length > 0 && (
              <Panel title={`Overdue (${overdue.length})`} flush
                description="Expected payments that haven't been matched — record them or chase them.">
                <ul className="divide-y divide-border">
                  {overdue.map((o: Outstanding) => (
                    <li key={o.movement_id}
                      className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
                      <IconTile label={o.label} />
                      <div className="min-w-0 flex-1">
                        <p className="text-[13.5px] font-medium">{o.label}</p>
                        <p className="text-[12px] text-fg-subtle">
                          due {formatSmartDate(o.due_date)} · {o.age_days}d late
                        </p>
                      </div>
                      <Money pence={o.outstanding_pence} className="font-medium text-warning" />
                    </li>
                  ))}
                </ul>
              </Panel>
            )}

            {totalDue === 0 && ruleItems.length === 0 && (
              <Panel>
                <EmptyState icon={Users2} title="Set up the shared pot"
                  description="Contribution rules describe how much each member pays into shared accounts — this page then tracks expected vs paid for everyone." />
              </Panel>
            )}
          </>
        )}
      </DataView>

      {editing !== null && (
        <ShareDialog
          rule={editing === "new" ? null : editing}
          accounts={accounts.data?.items ?? []}
          profiles={identity.data ?? []}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); refresh(); }}
        />
      )}
      {matching && (
        <MatchDialog
          movement={matching.movement}
          member={matching.member}
          onClose={() => setMatching(null)}
          onDone={() => { setMatching(null); refresh(); }}
        />
      )}
    </div>
  );
}

/** Create/edit a contribution share — a transfer rule into a shared
 * account (issue #277). */
function ShareDialog({ rule, accounts, profiles, onClose, onSaved }: {
  rule: Rule | null;
  accounts: Account[];
  profiles: Profile[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const isNew = rule === null;
  const acctOwner = (id: number) => accounts.find((a) => a.id === id)?.owner_profile_id ?? null;
  const [memberId, setMemberId] = useState(() => {
    if (rule) return String(acctOwner(rule.source_account_id) ?? "");
    return profiles.find((p) => p.is_active)?.id != null
      ? String(profiles.find((p) => p.is_active)!.id) : "";
  });
  const [name, setName] = useState(rule?.name ?? "");
  const [sourceId, setSourceId] = useState(String(rule?.source_account_id ?? ""));
  const [destId, setDestId] = useState(String(rule?.dest_account_id ?? ""));
  const [pounds, setPounds] = useState(() => {
    const p = (rule?.amount_spec as { pence?: number } | undefined)?.pence;
    return p != null ? String(p / 100) : "";
  });
  const [dom, setDom] = useState(String(rule?.day_of_month ?? 1));
  const [frequency, setFrequency] = useState<Frequency>(rule?.frequency ?? "monthly");
  const [busy, setBusy] = useState(false);

  const memberAccounts = accounts.filter((a) =>
    !a.archived && String(a.owner_profile_id ?? "") === memberId);
  const sharedAccounts = accounts.filter((a) =>
    !a.archived && a.owner_profile_id === null);
  const memberName = profiles.find((p) => String(p.id) === memberId)?.display_name ?? "Member";
  const effectiveName = name.trim() || `${memberName} → shared`;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!sourceId || !destId || !pounds) return;
    setBusy(true);
    const body = {
      name: effectiveName,
      source_account_id: Number(sourceId),
      dest_account_id: Number(destId),
      amount_spec: { kind: "fixed" as const, pence: Math.round(Number(pounds) * 100) },
      frequency,
      day_of_month: Number(dom),
      start_date: rule?.start_date ?? toISODate(new Date()),
    };
    const req = isNew
      ? api.post("/api/v1/transfer-rules", { body })
      : api.patch("/api/v1/transfer-rules/{rid}", {
          path: { rid: rule!.id },
          body: {
            name: body.name, amount_spec: body.amount_spec,
            frequency: body.frequency, day_of_month: body.day_of_month,
          },
        });
    req.then(() => {
        toast.success(isNew ? `${effectiveName} set` : `${effectiveName} updated`);
        onSaved();
      })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}
      title={isNew ? "Set a member's share" : `Edit ${rule!.name}`}
      description="A standing rule — it schedules the expected payment each period and this page tracks whether it lands."
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-share"
          disabled={busy || !sourceId || !destId || !pounds || Number(dom) < 1 || Number(dom) > 31}>
          {busy ? "Saving…" : isNew ? "Set share" : "Save"}
        </Button>
      </>}>
      <form id="ff-share" onSubmit={submit} className="grid gap-4">
        {isNew && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Member">{(id) => (
              <Select id={id} value={memberId} onChange={(e) => { setMemberId(e.target.value); setSourceId(""); }}>
                {profiles.filter((p) => p.is_active).map((p) => (
                  <option key={p.id} value={p.id}>{p.display_name}</option>
                ))}
              </Select>
            )}</Field>
            <Field label="From their account">{(id) => (
              <Select id={id} required value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
                <option value="" disabled>Choose…</option>
                {memberAccounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
            )}</Field>
          </div>
        )}
        {isNew && (
          <Field label="Into the shared account">{(id) => (
            <Select id={id} required value={destId} onChange={(e) => setDestId(e.target.value)}>
              <option value="" disabled>Choose…</option>
              {sharedAccounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select>
          )}</Field>
        )}
        <Field label="Rule name" hint={`Defaults to "${effectiveName}".`}>{(id) => (
          <Input id={id} value={name} onChange={(e) => setName(e.target.value)} placeholder={effectiveName} />
        )}</Field>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Amount (£)">{(id) => (
            <Input id={id} required type="number" step="0.01" min="0" aria-label="Share amount"
              value={pounds} onChange={(e) => setPounds(e.target.value)} />
          )}</Field>
          <Field label="Day of month">{(id) => (
            <Input id={id} required type="number" min="1" max="31"
              value={dom} onChange={(e) => setDom(e.target.value)} />
          )}</Field>
          <Field label="Frequency">{(id) => (
            <Select id={id} value={frequency} onChange={(e) => setFrequency(e.target.value as Frequency)}>
              <option value="monthly">Monthly</option>
              <option value="weekly">Weekly</option>
              <option value="fortnightly">Fortnightly</option>
              <option value="quarterly">Quarterly</option>
              <option value="annually">Annually</option>
            </Select>
          )}</Field>
        </div>
      </form>
    </Dialog>
  );
}

/** Record a real payment against an expected contribution — picks an
 * unmatched transfer transaction into the shared account. */
function MatchDialog({ movement, member, onClose, onDone }: {
  movement: Movement;
  member: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const ready = useReady();
  const txns = useApi(
    () => api.get("/api/v1/transactions", {
      query: {
        account_id: movement.account_id!,
        direction: "in",
        size: 50,
      },
    }), ready);
  const [txnId, setTxnId] = useState("");
  const [busy, setBusy] = useState(false);
  const items = txns.data?.items ?? [];

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!txnId) return;
    setBusy(true);
    api.post("/api/v1/expected-movements/{mid}/matches", {
      path: { mid: movement.id },
      body: { transaction_id: Number(txnId) },
    }).then(() => { toast.success("Payment recorded"); onDone(); })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}
      title={`Record ${member}'s payment`}
      description={<>Match a transfer into the shared account to <Money pence={movement.amount_pence} /> expected on {formatDayMonth(movement.expected_date)}.</>}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-match" disabled={busy || !txnId}>
          {busy ? "Recording…" : "Record payment"}
        </Button>
      </>}>
      <form id="ff-match" onSubmit={submit} className="grid gap-4">
        <Field label="Transfer transaction" hint="Incoming payments on the shared account.">
          {(id) => (
            <Select id={id} required value={txnId} onChange={(e) => setTxnId(e.target.value)}>
              <option value="" disabled>Choose…</option>
              {items.map((t: Txn) => (
                <option key={t.id} value={t.id}>
                  {formatDayMonth(t.date)} — {t.payee} — {formatPence(t.amount_pence)}
                </option>
              ))}
            </Select>
          )}
        </Field>
        {items.length === 0 && !txns.loading && (
          <p className="text-[12.5px] text-fg-subtle">
            No incoming payments on this account yet — once a transfer lands (entered or synced), it can be matched here.
          </p>
        )}
      </form>
    </Dialog>
  );
}
