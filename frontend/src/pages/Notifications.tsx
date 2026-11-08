/** Notifications (issue #281): the alert engine's two halves — the
 * events inbox (what fired, ack/snooze) and the rules that raise them
 * (spend_above / balance_below thresholds per account or household-
 * wide, digest batching, member targeting). Deliveries feed HA's
 * notify service; the inbox is the household-visible record. */
import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
  AlarmClockCheck, Bell, BellOff, Check, Plus, Send, Trash2,
} from "lucide-react";
import { api } from "../api/client";
import { useApi, useIdentity } from "../api/useApi";
import { useAccounts, useReady } from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Badge, type BadgeTone } from "../components/Badge";
import { Button } from "../components/Button";
import { Dialog } from "../components/Dialog";
import { Field, Input, Select } from "../components/Field";
import { IconTile, PageHeader, Segmented, Stat } from "../components/Display";
import { EmptyState, Skeleton } from "../components/Feedback";
import { Panel } from "../components/Panel";
import { cn } from "../lib/cn";
import { formatPence, formatSmartDate } from "../lib/format";

type Rule = components["schemas"]["NotificationRuleOut"];
type Event = components["schemas"]["NotificationEventOut"];
type Delivery = components["schemas"]["NotificationDeliveryOut"];

const STATE_TONE: Record<string, BadgeTone> = {
  pending: "warning", delivered: "info", acked: "success",
  snoozed: "neutral",
};
const KIND_LABEL: Record<string, string> = {
  spend_above: "Spend above", balance_below: "Balance below",
};

/** Friendly text for a rule's params — threshold + optional account. */
function ruleSummary(r: Rule, acctName: (id: number) => string): string {
  const p = (r.params ?? {}) as Record<string, unknown>;
  const threshold = Number(p.threshold_pence ?? 0);
  const acct = p.account_id != null ? acctName(Number(p.account_id)) : "any account";
  return r.kind === "spend_above"
    ? `any spend over ${formatPence(threshold)} on ${acct}`
    : `${acct} drops below ${formatPence(threshold)}`;
}

/** Friendly text for an event payload, by shape. */
function eventSummary(e: Event, acctName: (id: number) => string): {
  title: string; detail: string;
} {
  const p = (e.payload ?? {}) as Record<string, unknown>;
  if ("payee" in p && "amount_pence" in p) {
    return {
      title: String(p.payee),
      detail: `${formatSmartDate(String(p.date ?? ""))} · ${formatPence(Number(p.amount_pence))}`,
    };
  }
  if ("balance_pence" in p) {
    return {
      title: acctName(Number(p.account_id ?? 0)) || "Account",
      detail: `balance ${formatPence(Number(p.balance_pence))} vs threshold ${formatPence(Number(p.threshold_pence ?? 0))}`,
    };
  }
  return { title: `Event #${e.id}`, detail: e.dedup_key };
}

export function Notifications() {
  const ready = useReady();
  const [rev, setRev] = useState(0);
  const identity = useIdentity();
  const accounts = useAccounts(rev);
  const rules = useApi(() => api.get("/api/v1/notifications/rules"), ready, [rev]);
  const events = useApi(() => api.get("/api/v1/notifications/events"), ready, [rev]);
  const deliveries = useApi(() => api.get("/api/v1/notifications/deliveries"), ready, [rev]);
  const [scope, setScope] = useState<"open" | "all">("open");
  const [adding, setAdding] = useState(false);
  const refresh = () => setRev((r) => r + 1);

  const acctName = (id: number) =>
    accounts.data?.items.find((a) => a.id === id)?.name ?? "";

  const items = useMemo(() => {
    const all = Array.isArray(events.data) ? events.data : [];
    const open = all.filter((e) => e.state === "pending" || e.state === "delivered");
    return scope === "open" ? open : all;
  }, [events.data, scope]);

  const openCount = (Array.isArray(events.data) ? events.data : [])
    .filter((e) => e.state === "pending" || e.state === "delivered").length;
  const failedDeliveries = (Array.isArray(deliveries.data) ? deliveries.data : [])
    .filter((d) => d.status === "failed").length;

  const ack = (e: Event) =>
    api.post("/api/v1/notifications/events/{event_id}/ack",
      { path: { event_id: e.id } })
      .then(() => refresh())
      .catch((err: Error) => toast.error(err.message));

  const snooze = (e: Event, hours: number) => {
    const until = new Date(Date.now() + hours * 3600_000).toISOString();
    return api.post("/api/v1/notifications/events/{event_id}/snooze",
      { path: { event_id: e.id }, body: { until } })
      .then(() => { toast.success(`Snoozed ${hours >= 24 ? `${hours / 24}d` : `${hours}h`}`); refresh(); })
      .catch((err: Error) => toast.error(err.message));
  };

  const toggleRule = (r: Rule) =>
    api.patch("/api/v1/notifications/rules/{rule_id}", {
      path: { rule_id: r.id }, body: { enabled: !r.enabled },
    }).then(() => refresh())
      .catch((e: Error) => toast.error(e.message));

  const removeRule = (r: Rule) =>
    api.delete("/api/v1/notifications/rules/{rule_id}", {
      path: { rule_id: r.id },
    }).then(() => { toast.success(`"${r.name}" deleted`); refresh(); })
      .catch((e: Error) => toast.error(e.message));

  const evaluate = (r: Rule) =>
    api.post("/api/v1/notifications/rules/{rule_id}/evaluate",
      { path: { rule_id: r.id } })
      .then((res: unknown) => {
        const n = typeof res === "object" && res !== null
          ? Object.values(res as Record<string, number>).reduce((s, v) => s + (typeof v === "number" ? v : 0), 0)
          : 0;
        toast.success(n > 0 ? `Raised ${n} event${n === 1 ? "" : "s"}` : "Nothing to raise");
        refresh();
      }).catch((e: Error) => toast.error(e.message));

  const dispatch = () =>
    api.post("/api/v1/notifications/dispatch")
      .then(() => { toast.success("Dispatch run complete"); refresh(); })
      .catch((e: Error) => toast.error(e.message));

  return (
    <div>
      <PageHeader
        title="Notifications"
        description="Alerts the app raises — spend spikes, low balances — and who they reach."
        actions={
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" onClick={dispatch}>
              <Send /> Dispatch
            </Button>
            <Button variant="primary" size="sm" onClick={() => setAdding(true)}>
              <Plus /> New rule
            </Button>
          </div>
        }
      />

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Open alerts" value={String(openCount)}
          className="rounded-xl border border-border bg-surface p-4 shadow-card" />
        <Stat label="Active rules" value={String((rules.data ?? []).filter((r) => r.enabled).length)}
          className="rounded-xl border border-border bg-surface p-4 shadow-card" />
        <Stat label="Deliveries" value={String((deliveries.data ?? []).length)}
          hint={failedDeliveries ? `${failedDeliveries} failed` : "none failed"}
          className="rounded-xl border border-border bg-surface p-4 shadow-card" />
        <Stat label="Channel" value="HA notify"
          hint="Home Assistant service"
          className="hidden rounded-xl border border-border bg-surface p-4 shadow-card lg:block" />
      </div>

      {/* Inbox */}
      <Panel
        title="Inbox"
        description="Events raised by rules — ack when handled, snooze to park them."
        flush
        className="mb-4"
        actions={
          <Segmented label="Inbox scope" value={scope} onChange={setScope}
            options={[{ value: "open", label: "Open" }, { value: "all", label: "All" }]} />
        }
      >
        <DataView
          state={{ loading: events.loading, error: events.error, data: items }}
          empty={scope === "open" ? "Inbox zero" : "No events yet"}
          emptyIcon={AlarmClockCheck}
          emptyDescription={scope === "open" ? "Nothing needs your attention right now." : "Rules raise events here when their conditions hit."}
          loading={<Skeleton className="h-32 w-full" />}
        >
          {(list) => (
            <ul className="divide-y divide-border">
              {list.map((e) => {
                const s = eventSummary(e, acctName);
                const rule = (rules.data ?? []).find((r) => r.id === e.rule_id);
                const open = e.state === "pending" || e.state === "delivered";
                return (
                  <li key={e.id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-3">
                    <IconTile icon={Bell} label={s.title} size="sm" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13.5px] font-medium">{s.title}</p>
                      <p className="truncate text-[12px] text-fg-subtle">
                        {rule?.name ?? "Rule"} · {s.detail} · {formatSmartDate(e.created_at)}
                      </p>
                    </div>
                    <Badge tone={STATE_TONE[e.state] ?? "neutral"}>{e.state}</Badge>
                    {open && (
                      <div className="flex gap-1">
                        <Button variant="ghost" size="sm"
                          aria-label={`Snooze ${s.title}`}
                          onClick={() => snooze(e, 24)}>
                          <BellOff /> 1d
                        </Button>
                        <Button variant="ghost" size="icon-sm"
                          aria-label={`Acknowledge ${s.title}`}
                          title="Acknowledge"
                          onClick={() => ack(e)}>
                          <Check />
                        </Button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </DataView>
      </Panel>

      {/* Rules */}
      <Panel
        title="Alert rules"
        description="Conditions the app watches — each can batch into a digest and target a member."
        flush
      >
        {(rules.data ?? []).length === 0 ? (
          <EmptyState compact icon={Bell} title="No rules yet"
            description='Try "tell me when any spend is over £100" or "warn when Joint Bills drops below £200".' />
        ) : (
          <ul className="divide-y divide-border">
            {(rules.data ?? []).map((r) => (
              <li key={r.id}
                className={cn("flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-3", !r.enabled && "opacity-55")}>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13.5px] font-medium">{r.name}</p>
                  <p className="truncate text-[12px] text-fg-subtle">
                    {KIND_LABEL[r.kind] ?? r.kind} · {ruleSummary(r, acctName)}
                    {r.digest ? ` · digest ${r.digest_window_minutes}m` : " · immediate"}
                  </p>
                </div>
                {r.target_profile_id != null && (
                  <Badge tone="info">
                    {identity.data?.find((p) => p.id === r.target_profile_id)?.display_name ?? "member"}
                  </Badge>
                )}
                <Button variant="ghost" size="sm"
                  aria-label={`Evaluate ${r.name}`} title="Check now"
                  onClick={() => evaluate(r)}>
                  Check now
                </Button>
                <button
                  type="button" role="switch" aria-checked={r.enabled}
                  aria-label={`Enable ${r.name}`}
                  onClick={() => toggleRule(r)}
                  className={cn("relative h-5 w-9 rounded-full transition-colors",
                    r.enabled ? "bg-accent" : "border border-border bg-surface-2")}>
                  <span className={cn("absolute top-0.5 size-4 rounded-full bg-white shadow transition-all",
                    r.enabled ? "left-[18px]" : "left-0.5")} />
                </button>
                <Button variant="ghost" size="icon-sm"
                  aria-label={`Delete ${r.name}`}
                  onClick={() => removeRule(r)}>
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {adding && (
        <RuleDialog
          accounts={accounts.data?.items ?? []}
          profiles={identity.data ?? []}
          onClose={() => setAdding(false)}
          onSaved={() => { setAdding(false); refresh(); }}
        />
      )}
    </div>
  );
}

function RuleDialog({ accounts, profiles, onClose, onSaved }: {
  accounts: components["schemas"]["AccountOut"][];
  profiles: components["schemas"]["ProfileOut"][];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"spend_above" | "balance_below">("spend_above");
  const [pounds, setPounds] = useState("");
  const [accountId, setAccountId] = useState("");
  const [digest, setDigest] = useState(false);
  const [busy, setBusy] = useState(false);

  const effectiveName = name.trim() || (kind === "spend_above"
    ? `Spend over £${pounds || "…"}` : `Balance below £${pounds || "…"}`);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!pounds) return;
    setBusy(true);
    api.post("/api/v1/notifications/rules", {
      body: {
        name: effectiveName, kind,
        params: {
          threshold_pence: Math.round(Number(pounds) * 100),
          ...(accountId ? { account_id: Number(accountId) } : {}),
        },
        digest,
        channel: "ha_notify",
        digest_window_minutes: 60,
        privacy_safe: true,
      },
    }).then(() => { toast.success(`"${effectiveName}" created`); onSaved(); })
      .catch((err: Error) => { toast.error(err.message); setBusy(false); });
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}
      title="New alert rule"
      description="Raises an event in the inbox — and pushes via Home Assistant notify — when the condition hits."
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-nrule"
          disabled={busy || !pounds || Number(pounds) <= 0}>
          {busy ? "Creating…" : "Create rule"}
        </Button>
      </>}>
      <form id="ff-nrule" onSubmit={submit} className="grid gap-4">
        <Field label="Condition">{(_id) => (
          <Select aria-label="Rule kind" value={kind}
            onChange={(e) => setKind(e.target.value as typeof kind)}>
            <option value="spend_above">Any spend above £…</option>
            <option value="balance_below">Balance drops below £…</option>
          </Select>
        )}</Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Threshold (£)">{(_id) => (
            <Input aria-label="Threshold amount" required type="number" step="0.01" min="0"
              value={pounds} onChange={(e) => setPounds(e.target.value)} />
          )}</Field>
          <Field label="On account">{(_id) => (
            <Select aria-label="Rule account" value={accountId}
              onChange={(e) => setAccountId(e.target.value)}>
              <option value="">Any account</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </Select>
          )}</Field>
        </div>
        <Field label="Name" hint={`Defaults to "${effectiveName}".`}>{(_id) => (
          <Input aria-label="Rule name" value={name} placeholder={effectiveName}
            onChange={(e) => setName(e.target.value)} />
        )}</Field>
        <label className="flex cursor-pointer items-center gap-2.5 text-[13.5px]">
          <input type="checkbox" checked={digest}
            onChange={(e) => setDigest(e.target.checked)}
            className="size-4 rounded border-border accent-accent"
            aria-label="Batch into a digest" />
          <span>
            Batch into a digest
            <span className="ml-1.5 text-[12px] text-fg-subtle">— one notification per window instead of per event</span>
          </span>
        </label>
      </form>
    </Dialog>
  );
}
