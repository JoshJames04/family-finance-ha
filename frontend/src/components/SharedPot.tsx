/** SharedPot: the household bills pot, embedded in Budget (issue
 * #297). The split is a budgeting decision — who funds the pot and by
 * how much — so it lives with the monthly plan, period-scoped to the
 * viewed budget month. Member standing reads expected vs matched for
 * that period; the rule machinery sits behind a disclosure. */
import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
  ArrowLeftRight, Check, ChevronDown, HandCoins, Pencil, Plus,
  SlidersHorizontal, Trash2, Users2,
} from "lucide-react";
import { api } from "../api/client";
import { useApi, useIdentity, useMeProfileId } from "../api/useApi";
import { useAccounts, useReady } from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "./DataView";
import { Badge, type BadgeTone } from "./Badge";
import { Button } from "./Button";
import { Dialog } from "./Dialog";
import { Field, Input, Select } from "./Field";
import { IconTile, Progress, Segmented } from "./Display";
import { EmptyState } from "./Feedback";
import { Money } from "./Money";
import { Panel } from "./Panel";
import { cn } from "../lib/cn";
import { formatDayMonth, formatPence, toISODate } from "../lib/format";
import { toneFor } from "../lib/visuals";

type Profile = components["schemas"]["ProfileOut"];
type Payment = components["schemas"]["SharedContributionPayment"];
type Cycle = components["schemas"]["SharedContributionCycle"];
type Rule = components["schemas"]["TransferRuleOut"];
type Txn = components["schemas"]["TransactionOut"];
type Account = components["schemas"]["AccountOut"];
type Frequency = components["schemas"]["Frequency"];

function currentFixedAmount(rule: Rule): number {
  const spec = rule.amount_spec;
  const today = toISODate(new Date());
  if (!Array.isArray(spec.effective_amounts)) return Number(spec.pence ?? 0);
  const effective = spec.effective_amounts.slice().reverse().find(change => change.date <= today);
  return Number(effective?.pence ?? spec.original_pence ?? spec.pence ?? 0);
}

const STATE_TONE: Record<string, BadgeTone> = {
  satisfied: "success", partial: "warning", overdue: "error",
  outstanding: "info", scheduled: "info", none: "neutral",
  unmatched: "neutral",
};

/** Parse a rule's amount spec into a readable summary. */
function specLabel(spec: Rule["amount_spec"]): string {
  const k = spec.kind as string;
  if (k === "fixed") return "Fixed amount";
  if (k === "income_pct") return `${(Number(spec.bps ?? 0) / 100).toString()}% of income`;
  if (k === "budget_line") return "From budget line";
  return k;
}

export function SharedPot({ period, sharedTotal }: {
  period: { start: string; end: string };
  sharedTotal?: number;
}) {
  const ready = useReady();
  const identity = useIdentity();
  const me = useMeProfileId();
  const [rev, setRev] = useState(0);
  const accounts = useAccounts(rev);
  const rules = useApi(() => api.get("/api/v1/transfer-rules"), ready, [rev]);
  const [editing, setEditing] = useState<Rule | "new" | null>(null);
  const [removing, setRemoving] = useState<Rule | null>(null);
  const [matching, setMatching] = useState<{ movement: Payment; member: string } | null>(null);
  const [adjusting, setAdjusting] = useState(false);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [potId, setPotId] = useState<number | null>(null);
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
  /** The member a transfer rule belongs to — its source account owner. */
  const ruleOwner = (r: Rule) => acctById.get(r.source_account_id)?.owner_profile_id ?? null;
  /** Shared (pot) accounts: unowned accounts that rules pay into. */
  const sharedAccountIds = useMemo(() => {
    const ids = new Set<number>();
    for (const r of ruleItems) {
      if (acctById.get(r.dest_account_id)?.owner_profile_id === null) {
        ids.add(r.dest_account_id);
      }
    }
    return ids;
  }, [ruleItems, acctById]);

  /* ---- The split (issue #293): pot total + each member's share from
   * the fixed rules into the pot. Adjust patches those rules; rules
   * recur monthly so the split carries forward until changed. ---- */
  const pots = useMemo(
    () => [...sharedAccountIds]
      .map((id) => acctById.get(id)).filter((a): a is Account => !!a),
    [sharedAccountIds, acctById]);
  const pot = (potId != null ? acctById.get(potId) : undefined) ?? pots[0];

  const proposals = useApi(
    () => api.get("/api/v1/transfer-rules/shared-split/{account_id}/proposals", {
      path: { account_id: pot!.id },
    }), ready && pot != null, [pot?.id, rev]);
  const [deciding, setDeciding] = useState<string | null>(null);
  const decide = (id: string, decision: "accept" | "keep") => {
    if (!pot) return;
    setDeciding(id);
    api.post("/api/v1/transfer-rules/shared-split/{account_id}/proposals/{proposal_id}/{decision}", {
      path: { account_id: pot.id, proposal_id: id, decision },
    }).then(() => {
      toast.success(decision === "accept" ? "Contribution change accepted" : "Kept the agreed contribution");
      refresh();
    }).catch((error: Error) => { toast.error(error.message); proposals.refetch(); })
      .finally(() => setDeciding(null));
  };

  const cycles = useApi(
    () => api.get("/api/v1/expected-movements/contributions/cycles", {
      query: { start: period.start, end: period.end, account_id: pot?.id },
    }), ready, [period.start, period.end, pot?.id, rev]);
  const cycleByMember = new Map<number, Cycle>(
    (Array.isArray(cycles.data) ? cycles.data : []).map((cycle) => [cycle.profile_id, cycle]));
  const today = toISODate(new Date());
  const standings = (identity.data ?? []).filter((p) => p.is_active).map((profile) => {
    const cycle = cycleByMember.get(profile.id);
    const payments = cycle?.payments ?? [];
    const expected = payments.reduce((sum, p) => sum + p.expected_pence, 0);
    const matched = payments.reduce((sum, p) => sum + p.matched_pence, 0);
    const unpaid = payments.filter((p) => p.matched_pence < p.expected_pence);
    const memberRules = ruleItems.filter((r) =>
      ruleOwner(r) === profile.id && (pot == null || r.dest_account_id === pot.id));
    const state = expected === 0 ? (memberRules.length ? "scheduled" : "none")
      : unpaid.length === 0 ? "satisfied"
      : unpaid.some((p) => p.due_date < today) ? "overdue"
      : matched > 0 ? "partial" : "outstanding";
    return { profile, cycle, expected, matched, unpaid, state,
      outstanding: expected - matched, rules: memberRules };
  });

  const memberShare = useMemo(() => {
    const members = (identity.data ?? []).filter((p) => p.is_active);
    return members.map((p) => {
      const fixed = ruleItems.filter((r) =>
        ruleOwner(r) === p.id && pot != null && r.dest_account_id === pot.id
        && r.amount_spec.kind === "fixed" && r.frequency === "monthly");
      const custom = ruleItems.filter((r) =>
        ruleOwner(r) === p.id && pot != null && r.dest_account_id === pot.id
        && (r.amount_spec.kind !== "fixed" || r.frequency !== "monthly"));
      const pence = fixed.reduce(
        (s, r) => s + Math.abs(currentFixedAmount(r)), 0);
      return { profile: p, fixed, custom, pence };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity.data, ruleItems, pot, acctById]);
  const splitTotal = memberShare.reduce((s, m) => s + m.pence, 0);
  const customRules = memberShare.flatMap((m) => m.custom);

  const autoMatch = () =>
    api.post("/api/v1/expected-movements/match")
      .then((r) => {
        toast.success(r.matched > 0 ? `Matched ${r.matched} payment${r.matched === 1 ? "" : "s"}` : "No new matches found");
        refresh();
      }).catch((e: Error) => toast.error(e.message));

  const toggleRule = (r: Rule) =>
    api.patch("/api/v1/transfer-rules/{rid}", {
      path: { rid: r.id }, body: { active: !r.active } })
      .then(() => {
        toast.success(r.active ? `${r.name} paused` : `${r.name} resumed`);
        refresh();
      })
      .catch((e: Error) => toast.error(e.message));

  const removeRule = (r: Rule) =>
    api.delete("/api/v1/transfer-rules/{rid}", { path: { rid: r.id } })
      .then(() => {
        toast.success(`${r.name} removed`);
        setRemoving(null);
        refresh();
      })
      .catch((e: Error) => toast.error(e.message));

  return (
    <Panel
      title={pot ? `Shared pot — ${pot.name}` : "Shared pot"}
      description="Who funds this shared account and when their next payment is due."
      actions={
        <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2">
          {pots.length > 1 && (
            <Select aria-label="Shared pot" sizeVariant="sm"
              className="min-h-11" wrapperClassName="w-full sm:w-52"
              value={pot?.id ?? ""}
              onChange={(e) => setPotId(Number(e.target.value))}>
              {pots.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </Select>
          )}
          <Button variant="ghost" size="sm" className="min-h-11" onClick={autoMatch}>
            <ArrowLeftRight /> Match payments
          </Button>
          {pot != null && (
            <Button variant="secondary" size="sm" className="min-h-11" onClick={() => setAdjusting(true)}>
              <SlidersHorizontal /> Adjust split
            </Button>
          )}
          <Button variant="primary" size="sm" className="min-h-11" onClick={() => setEditing("new")}>
            <Plus /> Set a share
          </Button>
        </div>
      }
    >
      <DataView
        state={{
          loading: rules.loading || cycles.loading,
          retrying: rules.retrying || cycles.retrying,
          error: rules.error ?? cycles.error,
          data: "ready",
          refetch: () => { rules.refetch(); cycles.refetch(); },
        }}
        empty=""
      >
        {() => (
          <>
            {Array.isArray(proposals.data) && proposals.data.length > 0 && (
              <section aria-label="Proposed contribution changes" className="mb-4 grid gap-2">
                {proposals.data.map(proposal => (
                  <div key={proposal.id} className="rounded-lg border border-border bg-surface-2 p-3">
                    <p className="font-medium">
                      {identity.data?.find(member => member.id === proposal.profile_id)?.display_name ?? "Member"}
                      {" · "}<Money pence={proposal.old_pence} /> to <Money pence={proposal.new_pence} />
                    </p>
                    <p className="text-sm text-fg-muted">
                      {proposal.reason} · Effective {formatDayMonth(proposal.effective_date)}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <Button variant="primary" size="sm" disabled={deciding != null}
                        onClick={() => decide(proposal.id, "accept")}>Accept change</Button>
                      <Button variant="secondary" size="sm" disabled={deciding != null}
                        onClick={() => decide(proposal.id, "keep")}>Keep agreed amount</Button>
                    </div>
                  </div>
                ))}
              </section>
            )}
            {/* The split — the household decision: one total, each
             * member's £ and % share, repeats monthly until changed. */}
            {pot != null && splitTotal > 0 && (
              <div className="mb-4">
                <div className="mb-2 flex items-baseline justify-between">
                  <p className="tnum flex flex-wrap items-baseline gap-x-1.5 text-[18px] font-semibold">
                    <Money pence={splitTotal} />
                    <span className="text-[12px] font-normal text-fg-subtle">
                      per month — repeats until you change it
                    </span>
                  </p>
                </div>
                <div className="mb-3 flex h-2.5 w-full overflow-hidden rounded-full bg-surface-3"
                  role="img"
                  aria-label={memberShare.map((m) =>
                    `${m.profile.display_name} ${Math.round(m.pence / splitTotal * 100)}%`).join(", ")}>
                  {memberShare.filter((m) => m.pence > 0).map((m) => (
                    <span key={m.profile.id}
                      className={cn("h-full", toneFor(m.profile.display_name).split(" ")[0])}
                      style={{ width: `${(m.pence / splitTotal) * 100}%` }} />
                  ))}
                </div>
                <ul className="grid min-w-0 gap-3 sm:grid-cols-2">
                  {memberShare.map((m) => (
                    <li key={m.profile.id} className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] items-center gap-x-2 gap-y-1">
                      <span className={cn("size-2.5 rounded-full",
                        toneFor(m.profile.display_name).split(" ")[0],
                        m.pence === 0 && "opacity-25")} />
                      <span className="min-w-0 wrap-anywhere text-[13px] font-medium">
                        {m.profile.display_name}
                        {m.profile.id === me && <span className="text-fg-subtle"> (you)</span>}
                      </span>
                      <span className="tnum col-start-2 text-[13px]">
                        <Money pence={m.pence} />
                        <span className="ml-1 text-[12px] text-fg-subtle">
                          {Math.round(m.pence / splitTotal * 100)}%
                        </span>
                      </span>
                      {m.custom.length > 0 && (
                        <span className="col-start-2"><Badge tone="info">{specLabel(m.custom[0]!.amount_spec)}</Badge></span>
                      )}
                    </li>
                  ))}
                </ul>
                {customRules.length > 0 && (
                  <p className="mt-2 text-[12px] text-fg-subtle">
                    {customRules.length} share rule{customRules.length === 1 ? "" : "s"} outside the fixed monthly split.
                  </p>
                )}
              </div>
            )}

            {/* Member standing for the relevant pay cycle */}
            <div className="grid gap-3 sm:grid-cols-2">
              {standings.map((m) => (
                <div key={m.profile.id}
                  className={cn("min-w-0 rounded-lg border border-border bg-surface-2/40 p-3.5",
                    m.profile.id === me && "ring-1 ring-accent/40")}>
                  <div className="mb-2 flex items-center gap-2.5">
                    <span className={cn("grid size-8 shrink-0 place-items-center rounded-full text-[12px] font-semibold",
                      toneFor(m.profile.display_name).split(" ").slice(0, 2).join(" "))}>
                      {m.profile.display_name.slice(0, 1).toUpperCase()}
                    </span>
                    <p className="min-w-0 flex-1 wrap-anywhere text-[13.5px] font-medium">
                      {m.profile.display_name}
                      {m.profile.id === me && <span className="ml-1 text-[12px] font-normal text-fg-subtle">(you)</span>}
                    </p>
                    <Badge tone={STATE_TONE[m.state] ?? "neutral"}>
                      {m.state === "none" ? "No share" : m.state === "satisfied" ? "Paid"
                        : m.state === "partial" ? "Part paid" : m.state === "outstanding" ? "Due" : m.state}
                    </Badge>
                  </div>
                  {m.cycle && (
                    <p className="mb-2 text-[12px] text-fg-subtle">
                      {m.cycle.basis === "pay_cycle" ? "Pay cycle" : "Calendar period"}{" "}
                      {formatDayMonth(m.cycle.start)} – {formatDayMonth(m.cycle.end)}
                      {m.cycle.basis === "calendar" && " · Configure a pay-cycle source in Budget"}
                    </p>
                  )}
                  {m.expected > 0 ? (
                    <>
                      <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1 text-[12.5px]">
                        <span className="inline-flex flex-wrap items-baseline gap-x-1 text-fg-muted">
                          <Money pence={m.matched} /> of <Money pence={m.expected} />
                        </span>
                        {m.outstanding > 0 && (
                          <span className="text-warning"><Money pence={m.outstanding} /> to go</span>
                        )}
                      </div>
                      <Progress value={m.matched} max={m.expected}
                        label={`${m.profile.display_name} contribution`} />
                      {m.unpaid.length > 0 && (
                        <div className="mt-2 flex justify-end">
                          <Button size="sm" variant="secondary" className="min-h-11"
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
                    <p className="wrap-anywhere text-[12.5px] text-fg-subtle">
                      {m.rules.length > 0
                        ? "Nothing due this cycle."
                        : `No share set — use Set a share to add ${m.profile.display_name}'s.`}
                    </p>
                  )}
                  {m.cycle?.next_payment && (() => {
                    const next = m.cycle.next_payment;
                    const remaining = next.expected_pence - next.matched_pence;
                    return (
                      <p className="mt-3 border-t border-border pt-2 text-[12.5px] text-fg-muted">
                        Next payment · {formatDayMonth(next.due_date)} ·{" "}
                        {remaining === 0 ? "Paid early" : <>
                          <Money pence={remaining} /> {next.matched_pence > 0 ? "left to pay" : "due"}
                        </>}
                      </p>
                    );
                  })()}
                </div>
              ))}
            </div>

            {ruleItems.length === 0 && inactiveRules.length === 0 && (
              <EmptyState compact icon={Users2} title="No shares set"
                description="Set each member's monthly share — the pot tracks expected vs paid for everyone." />
            )}

            {/* The standing orders behind the split — tucked away for
             * power use (different days/frequencies, custom specs). */}
            {ruleItems.length + inactiveRules.length > 0 && (
              <div className="mt-4 border-t border-border pt-3">
                <button type="button" onClick={() => setRulesOpen((o) => !o)}
                  aria-expanded={rulesOpen}
                  className="flex items-center gap-1.5 text-[12.5px] font-medium text-fg-muted hover:text-fg">
                  <ChevronDown className={cn("size-3.5 transition-transform", !rulesOpen && "-rotate-90")} />
                  Share rules ({ruleItems.length + inactiveRules.length})
                </button>
                {rulesOpen && (
                  <ul className="mt-2 divide-y divide-border">
                    {[...ruleItems, ...inactiveRules].map((r) => {
                      const owner = ruleOwner(r);
                      const ownerName = identity.data?.find((p) => p.id === owner)?.display_name
                        ?? (owner == null ? "Shared" : `Member ${owner}`);
                      const src = acctById.get(r.source_account_id);
                      const dst = acctById.get(r.dest_account_id);
                      return (
                        <li key={r.id} className={cn("flex flex-wrap items-center gap-x-4 gap-y-2 py-2.5", !r.active && "opacity-60")}>
                          <IconTile icon={HandCoins} label={r.name} size="sm" />
                          <div className="min-w-0 flex-1 basis-48">
                            <p className="wrap-anywhere text-[13px] font-medium">{r.name}</p>
                            <p className="wrap-anywhere text-[12px] text-fg-subtle">
                              {ownerName} · {src?.name ?? "?"} → {dst?.name ?? "?"} ·
                              {" "}{r.frequency} · day {r.day_of_month}
                            </p>
                          </div>
                          {r.amount_spec.kind === "fixed"
                            ? <Money pence={currentFixedAmount(r)} className="text-[13px] font-medium" />
                            : <Badge tone="info">{specLabel(r.amount_spec)}</Badge>}
                          <div className="flex gap-1">
                            <Button size="sm" variant="ghost" onClick={() => setEditing(r)}
                              aria-label={`Edit ${r.name}`}><Pencil /></Button>
                            <Button size="sm" variant="ghost" onClick={() => toggleRule(r)}
                              aria-label={`Pause ${r.name}`}>{r.active ? "Pause" : "Resume"}</Button>
                            <Button size="sm" variant="ghost" onClick={() => setRemoving(r)}
                              aria-label={`Remove ${r.name}`}><Trash2 /></Button>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            )}
          </>
        )}
      </DataView>

      {adjusting && pot != null && (
        <SplitDialog
          pot={pot}
          shares={memberShare}
          canConfigureApproval={identity.data?.some(member => member.id === me && member.role === "admin") ?? false}
          sharedTotal={sharedTotal}
          accounts={accounts.data?.items ?? []}
          onClose={() => setAdjusting(false)}
          onSaved={() => { setAdjusting(false); refresh(); }}
        />
      )}
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
      <Dialog open={removing !== null}
        onOpenChange={(o) => { if (!o) setRemoving(null); }}
        title={`Remove ${removing?.name ?? "rule"}?`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRemoving(null)}>Cancel</Button>
            <Button variant="primary" onClick={() => removing && removeRule(removing)}>
              Remove
            </Button>
          </>
        }>
        <p className="text-[13px] text-fg-subtle">
          The rule and its unfulfilled expected payments are deleted.
          Payments already matched against it stay in history. Pausing
          keeps the rule but stops future payments.
        </p>
      </Dialog>
    </Panel>
  );
}

type MemberShare = {
  profile: Profile;
  fixed: Rule[];
  custom: Rule[];
  pence: number;
};

/** Adjust the pot split (issue #293): edit by amount or by percentage;
 * on save each member's fixed rule into the pot is patched, created,
 * or paused to match. The rules recur monthly — the split carries
 * forward until next edited. */
function SplitDialog({ pot, shares, sharedTotal, accounts, canConfigureApproval, onClose, onSaved }: {
  pot: Account;
  shares: MemberShare[];
  canConfigureApproval: boolean;
  sharedTotal?: number;
  accounts: Account[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const total0 = shares.reduce((sum, m) => sum + m.pence, 0);
  const guided = sharedTotal != null && sharedTotal > 0;
  const target = guided ? sharedTotal : total0;
  const setup = useApi(() => guided
    ? api.get("/api/v1/transfer-rules/shared-split/{account_id}", { path: { account_id: pot.id } })
    : Promise.resolve(null), guided, [pot.id]);
  const policy = setup.data?.policy;
  type SplitMode = "amount" | "equal" | "percent" | "fixed_remainder";
  const [chosenMode, setMode] = useState<SplitMode | null>(null);
  const mode = chosenMode ?? policy?.mode ?? "amount";
  const [chosenRemainder, setRemainder] = useState<number | null>(null);
  const remainder = chosenRemainder ?? policy?.remainder_profile_id ?? shares[0]?.profile.id;
  const policyError = guided && setup.error;
  type Approval = "contributor" | "delegated" | "automatic";
  const [chosenApproval, setApproval] = useState<Approval | null>(null);
  const [chosenApprover, setApprover] = useState<number | null>(null);
  const approval = chosenApproval ?? policy?.approval ?? "contributor";
  const approver = chosenApprover ?? policy?.approver_profile_id;
  const [editedPcts, setPcts] = useState<Record<number, string>>(() => {
    let remaining = 100;
    return Object.fromEntries(shares.map((m, index) => {
      const share = total0 > 0 ? m.pence / total0 : 1 / shares.length;
      const value = index === shares.length - 1 ? remaining : Math.min(remaining, Math.round(share * 100));
      remaining -= value;
      return [m.profile.id, String(value)];
    }));
  });
  const pcts: Record<number, string> = {
    ...editedPcts,
    ...(chosenMode === null && policy?.mode === "percent"
      ? Object.fromEntries(Object.entries(policy.percentages ?? {}).map(([id, bps]) => [id, String(bps / 100)]))
      : {}),
  };
  const [amounts, setAmounts] = useState<Record<number, string>>(() => {
    let remaining = target;
    return Object.fromEntries(shares.map((m, index) => {
      const ratio = total0 > 0 ? m.pence / total0 : 1 / shares.length;
      const pence = !guided ? m.pence : index === shares.length - 1
        ? remaining : Math.min(remaining, Math.round(target * ratio));
      remaining -= pence;
      return [m.profile.id, String(pence / 100)];
    }));
  });
  const [fixedAmounts, setFixedAmounts] = useState<Record<number, string>>(
    () => Object.fromEntries(shares.map((member) => [member.profile.id, String(member.pence / 100)])),
  );
  const [totalPounds, setTotalPounds] = useState(String(target / 100));
  const [days, setDays] = useState<Record<number, number>>({});
  const [payday, setPayday] = useState<Record<number, boolean>>({});
  const [sources, setSources] = useState<Record<number, number>>({});
  const sourceOptions = (id: number) =>
    accounts.filter((a) => !a.archived && a.owner_profile_id === id);
  const sourceFor = (id: number) => {
    const options = sourceOptions(id);
    return sources[id] ?? (options.length === 1 ? options[0]?.id : undefined);
  };
  const [busy, setBusy] = useState(false);
  const nextPayday = (id: number) => setup.data?.next_paydays[id];
  const manualDate = (day: number) => {
    const now = new Date();
    const first = new Date(now.getFullYear(), now.getMonth(), 1);
    const on = (month: Date) => new Date(month.getFullYear(), month.getMonth(),
      Math.min(day, new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate()));
    const inMonth = on(first);
    return toISODate(inMonth) >= toISODate(now) ? toISODate(inMonth)
      : toISODate(on(new Date(first.getFullYear(), first.getMonth() + 1, 1)));
  };

  const toPence = (s: string) => Math.round(Number(s || "0") * 100);
  const amountTotal = shares.reduce((s, m) => s + toPence(amounts[m.profile.id] ?? ""), 0);
  const weight = (id: number) => Math.round(Number(pcts[id] || "0") * 100);
  const totalWeight = shares.reduce((sum, member) => sum + weight(member.profile.id), 0);
  const percentOk = totalWeight === 10000 && shares.every((m) =>
    weight(m.profile.id) >= 0 && weight(m.profile.id) <= 10000);
  const percentTotal = toPence(totalPounds);
  const fixedRemainder = target - shares.reduce((sum, member) => sum
    + (member.profile.id === remainder ? 0 : toPence(fixedAmounts[member.profile.id] ?? "")), 0);
  let cumulative = 0;
  let allocated = 0;
  const targets = Object.fromEntries([...shares].sort((a, b) => a.profile.id - b.profile.id).map((m) => {
    const id = m.profile.id;
    let pence = toPence(amounts[id] ?? "");
    if (mode === "fixed_remainder") {
      pence = id === remainder ? fixedRemainder : toPence(fixedAmounts[id] ?? "");
    }
    if (mode === "equal" || mode === "percent") {
      cumulative += mode === "equal" ? 1 : weight(id);
      const next = Math.round((guided ? target : percentTotal) * cumulative
        / (mode === "equal" ? shares.length : 10000));
      pence = next - allocated;
      allocated = next;
    }
    return [id, pence];
  }));
  const previewTotal = mode === "fixed_remainder" ? target : amountTotal;
  const otherSchedules = guided && shares.some((m) => m.custom.length > 0 || m.fixed.length > 1);
  const canSave = shares.length > 0 && !otherSchedules && !policyError && (!guided || !setup.loading)
    && (approval !== "delegated" || approver != null)
    && (mode !== "amount" || !guided || amountTotal === target)
    && (mode !== "percent" || percentOk)
    && (mode !== "fixed_remainder" || fixedRemainder >= 0)
    && shares.every((m) => ((targets[m.profile.id] ?? 0) <= 0 && (!guided || mode === "amount")) || m.fixed[0]
      || (guided ? sourceFor(m.profile.id) != null && ((payday[m.profile.id] && nextPayday(m.profile.id))
        || ((days[m.profile.id] ?? 0) >= 1 && (days[m.profile.id] ?? 0) <= 31)) : sourceOptions(m.profile.id).length > 0));

  const setAmount = (id: number, value: string) => {
    if (mode === "fixed_remainder") {
      setFixedAmounts((current) => ({ ...current, [id]: value }));
      return;
    }
    setAmounts((current) => {
      const next = { ...current, [id]: value };
      const other = shares.length === 2 ? shares.find((m) => m.profile.id !== id) : undefined;
      if (guided && mode === "amount" && other && value !== "") next[other.profile.id] = String(Math.max(0, target - toPence(value)) / 100);
      return next;
    });
  };

  // Two members: editing one % auto-balances the other to 100.
  const setPct = (id: number, v: string) => {
    setMode("percent");
    setPcts(() => {
      const next = { ...pcts, [id]: v };
      if (shares.length === 2) {
        const other = shares.find((m) => m.profile.id !== id);
        if (other && v !== "") {
          next[other.profile.id] = String(Math.max(0, 100 - Number(v)));
        }
      }
      return next;
    });
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSave) return;
    setBusy(true);
    if (guided) {
      api.post("/api/v1/transfer-rules/shared-split", {
        body: {
          account_id: pot.id,
          target_pence: target,
          policy: {
            mode,
            remainder_profile_id: mode === "fixed_remainder" ? remainder : undefined,
            approval: canConfigureApproval ? approval : undefined,
            approver_profile_id: canConfigureApproval && approval === "delegated" ? approver : undefined,
          },
          shares: shares.map((m) => ({
            profile_id: m.profile.id,
            rule_id: m.fixed[0]?.id,
            source_account_id: sourceFor(m.profile.id),
            day_of_month: days[m.profile.id],
            on_member_payday: payday[m.profile.id] ?? false,
            pence: targets[m.profile.id] ?? 0,
            percentage_bps: mode === "percent" ? weight(m.profile.id) : undefined,
          })),
        },
      }).then(() => {
        toast.success("Split updated — applies every month from now on");
        onSaved();
      }).catch((err: Error) => { toast.error(err.message); setBusy(false); });
      return;
    }
    const jobs: Promise<unknown>[] = [];
    for (const m of shares) {
      const pence = targets[m.profile.id] ?? 0;
      const [primary, ...extras] = m.fixed;
      if (primary) {
        jobs.push(
          pence === 0
            ? api.delete("/api/v1/transfer-rules/{rid}", { path: { rid: primary.id } })
            : api.patch("/api/v1/transfer-rules/{rid}", {
                path: { rid: primary.id },
                body: { amount_spec: { kind: "fixed", pence } },
              }));
        // Duplicate fixed rules would double-count the share.
        for (const extra of extras) {
          jobs.push(api.delete("/api/v1/transfer-rules/{rid}", { path: { rid: extra.id } }));
        }
      } else if (pence > 0) {
        const src = accounts.find((a) =>
          !a.archived && a.owner_profile_id === m.profile.id);
        if (!src) continue; // nowhere to pay from — surface via toast
        const day = shares.find((x) => x.fixed[0])?.fixed[0]?.day_of_month ?? 15;
        jobs.push(api.post("/api/v1/transfer-rules", {
          body: {
            name: `${m.profile.display_name} → ${pot.name}`,
            source_account_id: src.id,
            dest_account_id: pot.id,
            amount_spec: { kind: "fixed", pence },
            frequency: "monthly",
            day_of_month: day,
            start_date: toISODate(new Date()),
          },
        }));
      }
    }
    Promise.all(jobs)
      .then(() => { toast.success("Split updated — applies every month from now on"); onSaved(); })
      .catch((err: Error) => { toast.error(err.message); setBusy(false); });
  };

  return (
    <Dialog open className="wrap-anywhere [&_button]:min-h-11 [&_input]:min-h-11 [&_select]:min-h-11" onOpenChange={(o) => { if (!o) onClose(); }}
      title={`Adjust the ${pot.name} split`}
      description="How much goes into the pot each month, and who covers what. It repeats monthly until you change it."
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-split"
          disabled={busy || !canSave}>
          {busy ? "Saving…" : "Save split"}
        </Button>
      </>}>
      <form id="ff-split" onSubmit={submit} className="grid gap-4">
        <Segmented label="Adjust by" className="min-w-0 flex-wrap" value={mode} onChange={(next) => { setPcts(pcts); setMode(next); }}
          options={[
            { value: "amount", label: "By amount" },
            { value: "percent", label: "By percentage" },
            ...(guided ? [
              { value: "equal" as const, label: "Equal shares" },
              { value: "fixed_remainder" as const, label: "Fixed plus remainder" },
            ] : []),
          ]} />
        {guided && canConfigureApproval && (
          <Field label="Future contribution changes">{(id) => (
            <Select id={id} value={approval} onChange={event => setApproval(event.target.value as Approval)}>
              <option value="contributor">Ask each affected contributor</option>
              <option value="delegated">Ask one household approver</option>
              <option value="automatic">Apply automatically and notify</option>
            </Select>
          )}</Field>
        )}
        {guided && canConfigureApproval && approval === "delegated" && (
          <Field label="Household approver">{(id) => (
            <Select id={id} value={approver ?? ""} onChange={event => setApprover(Number(event.target.value))}>
              <option value="" disabled>Choose a household member</option>
              {shares.filter(member => member.profile.role !== "viewer").map(member => <option key={member.profile.id} value={member.profile.id}>
                {member.profile.display_name}
              </option>)}
            </Select>
          )}</Field>
        )}
        {policyError && <p role="alert">Could not load the agreed split. Close and try again.</p>}
        {mode === "fixed_remainder" && (
          <Field label="Who covers the remainder?">{(id) => (
            <Select id={id} value={remainder} onChange={(e) => setRemainder(Number(e.target.value))}>
              {shares.map((member) => <option key={member.profile.id} value={member.profile.id}>
                {member.profile.display_name}
              </option>)}
            </Select>
          )}</Field>
        )}
        {otherSchedules && (
          <p role="alert" className="text-[12.5px] text-warning">
            Use one fixed monthly share per person to allocate the Shared budget total.
            Review the existing share rules before saving.
          </p>
        )}
        {guided && (
          <p className="text-[13px] text-fg-muted">
            Shared budget total <Money pence={target} className="font-medium text-fg" />
          </p>
        )}
        {mode === "percent" && (
          <Field label="Total into the pot each month (£)">{(id) => (
            <Input id={id} required type="number" step="0.01" min="0"
              aria-label="Total monthly amount"
              value={totalPounds} readOnly={guided} onChange={(e) => setTotalPounds(e.target.value)} />
          )}</Field>
        )}
        <ul className="grid gap-3">
          {shares.map((m) => {
            const day = days[m.profile.id];
            const date = payday[m.profile.id] ? nextPayday(m.profile.id)
              : day ? manualDate(day) : undefined;
            return (
            <li key={m.profile.id} className="flex min-w-0 flex-wrap items-center gap-3">
              <span className={cn("grid size-8 shrink-0 place-items-center rounded-full text-[12px] font-semibold",
                toneFor(m.profile.display_name).split(" ").slice(0, 2).join(" "))}>
                {m.profile.display_name.slice(0, 1).toUpperCase()}
              </span>
              <span className="min-w-0 flex-1 basis-32 wrap-anywhere text-[13.5px] font-medium">
                {m.profile.display_name}
              </span>
              {guided && !m.fixed.length && (mode !== "amount" || (targets[m.profile.id] ?? 0) > 0) && (
                <>
                <Select aria-label={`${m.profile.display_name} payment account`}
                  value={sourceFor(m.profile.id) ?? ""}
                  onChange={(e) => setSources((current) => ({
                    ...current, [m.profile.id]: Number(e.target.value),
                  }))}>
                  <option value="" disabled>Choose payment account</option>
                  {sourceOptions(m.profile.id).map((account) => (
                    <option key={account.id} value={account.id}>{account.name}</option>
                  ))}
                </Select>
                {nextPayday(m.profile.id) && <label className="flex items-center gap-2 text-[13px]">
                  <input type="checkbox" checked={payday[m.profile.id] ?? false}
                    onChange={(e) => setPayday((current) => ({ ...current, [m.profile.id]: e.target.checked }))} />
                  Use {m.profile.display_name}’s configured payday
                </label>}
                {!payday[m.profile.id] && <Input type="number" min="1" max="31" placeholder="Day of month"
                  aria-label={`${m.profile.display_name} payment day of month`}
                  value={days[m.profile.id] ?? ""}
                  onChange={(e) => setDays((current) => ({
                    ...current, [m.profile.id]: Number(e.target.value),
                  }))} />}
                </>
              )}
              {date && <span className="text-[13px] text-fg-muted">Starts {formatDayMonth(date)}</span>}
              {mode === "equal" || (mode === "fixed_remainder" && remainder === m.profile.id) ? (
                <Money pence={targets[m.profile.id] ?? 0} />
              ) : mode === "amount" || mode === "fixed_remainder" ? (
                <>
                  <Input aria-label={`${m.profile.display_name} share amount`}
                    type="number" step="0.01" min="0" max={guided ? target / 100 : undefined}
                    className="min-h-11 w-full sm:w-28"
                    placeholder="0.00"
                    value={(mode === "fixed_remainder" ? fixedAmounts : amounts)[m.profile.id] ?? ""}
                    onChange={(e) => setAmount(m.profile.id, e.target.value)} />
                  <span className="tnum w-14 text-right text-[12.5px] text-fg-subtle">
                    {previewTotal > 0
                      ? `${Math.round(((targets[m.profile.id] ?? 0) / previewTotal) * 100)}%`
                      : "—"}
                  </span>
                </>
              ) : (
                <>
                  <Input aria-label={`${m.profile.display_name} share percent`}
                    type="number" step="0.01" min="0" max="100" className="min-h-11 w-full sm:w-20"
                    value={pcts[m.profile.id] ?? ""}
                    onChange={(e) => setPct(m.profile.id, e.target.value)} />
                  <span className="text-[12.5px] text-fg-subtle">%</span>
                  <span className="tnum flex-1 text-right text-[13px]">
                    ≈ <Money pence={targets[m.profile.id] ?? 0} />
                  </span>
                </>
              )}
            </li>
            );
          })}
        </ul>
        {mode === "amount" ? (
          <p className="text-[13px] text-fg-subtle">
            Allocated <Money pence={amountTotal} className="font-medium text-fg" /> per month
            {guided && amountTotal !== target && (
              <span role="alert"> · Shares must add up to the Shared budget total.</span>
            )}
          </p>
        ) : mode === "fixed_remainder" && fixedRemainder < 0 ? (
          <p role="alert">Fixed shares exceed the Shared budget total.</p>
        ) : mode === "percent" && !percentOk ? (
          <p role="alert" className="text-[12.5px] text-warning">
            Percentages must add to 100 — currently {totalWeight / 100}.
          </p>
        ) : null}
      </form>
    </Dialog>
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
    return rule ? String(currentFixedAmount(rule) / 100) : "";
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
            name: body.name,
            source_account_id: body.source_account_id,
            dest_account_id: body.dest_account_id,
            amount_spec: body.amount_spec.pence === currentFixedAmount(rule!)
              ? undefined : body.amount_spec,
            frequency: body.frequency,
            day_of_month: body.day_of_month !== (rule!.day_of_month ?? 1)
              || body.frequency !== rule!.frequency ? body.day_of_month : undefined,
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
    <Dialog open className="wrap-anywhere [&_button]:min-h-11 [&_input]:min-h-11 [&_select]:min-h-11" onOpenChange={(o) => { if (!o) onClose(); }}
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
        <Field label="Into the shared account" hint={isNew ? undefined : "Any account is allowed when editing."}>{(id) => (
          <Select id={id} required value={destId} onChange={(e) => setDestId(e.target.value)}>
            <option value="" disabled>Choose…</option>
            {(isNew ? sharedAccounts : accounts.filter((a) => !a.archived && String(a.id) !== sourceId))
              .map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Select>
        )}</Field>
        <Field label="Rule name" hint={`Defaults to "${effectiveName}".`}>{(id) => (
          <Input id={id} value={name} onChange={(e) => setName(e.target.value)} placeholder={effectiveName} />
        )}</Field>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Amount (£)">{(id) => (
            <Input id={id} required type="number" step="0.01" min="0" aria-label="Share amount"
              value={pounds} onChange={(e) => setPounds(e.target.value)} />
          )}</Field>
          <Field label="Day of month" hint={rule?.amount_spec.on_member_payday === true
            ? "Follows member payday. Change the day or frequency to use a fixed schedule." : undefined}>{(id) => (
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
  movement: Payment;
  member: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const ready = useReady();
  const txns = useApi(
    () => api.get("/api/v1/transactions", {
      query: {
        account_id: movement.account_id!,
        direction: "income",
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
      path: { mid: movement.movement_id },
      body: { transaction_id: Number(txnId) },
    }).then(() => { toast.success("Payment recorded"); onDone(); })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };

  return (
    <Dialog open className="wrap-anywhere [&_button]:min-h-11 [&_input]:min-h-11 [&_select]:min-h-11" onOpenChange={(o) => { if (!o) onClose(); }}
      title={`Record ${member}'s payment`}
      description={<>Match a transfer into the shared account to <Money pence={movement.expected_pence} /> expected on {formatDayMonth(movement.due_date)}.</>}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-match" className="min-h-11" disabled={busy || !txnId}>
          {busy ? "Recording…" : "Record payment"}
        </Button>
      </>}>
      <form id="ff-match" onSubmit={submit} className="grid gap-4">
        <Field label="Transfer transaction" hint="Incoming payments on the shared account.">
          {(id) => (
            <Select id={id} required className="min-h-11" value={txnId} onChange={(e) => setTxnId(e.target.value)}>
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
