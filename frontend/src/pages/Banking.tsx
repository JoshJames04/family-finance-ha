/** Banking: the Starling connect → discover → map → sync journey
 * (issues #269–#271). Tokens are write-only — validated then vaulted;
 * never rendered back. Discovered provider accounts link to internal
 * accounts, get created, or are ignored; sync runs report per-account
 * fetched/inserted/deduped counts and surface merge candidates in the
 * Review inbox. */
import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
  Building2, Check, Eye, EyeOff, History, Landmark, Link2,
  RefreshCw, RotateCcwKey, ShieldCheck, Trash2, X,
} from "lucide-react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { useAccounts, useReady } from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Badge, type BadgeTone } from "../components/Badge";
import { Button } from "../components/Button";
import { Dialog } from "../components/Dialog";
import { Field, Input, Select } from "../components/Field";
import { IconTile, PageHeader, Stat } from "../components/Display";
import { EmptyState, Skeleton } from "../components/Feedback";
import { Money } from "../components/Money";
import { Panel } from "../components/Panel";
import { cn } from "../lib/cn";
import { formatDateTime, formatRelativeDays } from "../lib/format";

type Connection = components["schemas"]["BankConnectionOut"];
type Mapping = components["schemas"]["BankMappingOut"];
type SyncRun = components["schemas"]["SyncRunOut"];
type Account = components["schemas"]["AccountOut"];

const STATUS_TONE: Record<string, BadgeTone> = {
  connected: "success", degraded: "warning", pending: "info",
  revoked: "error",
};

const STATUS_COPY: Record<string, string> = {
  connected: "Connected", degraded: "Degraded — will retry",
  pending: "Pending", revoked: "Disconnected",
};

export function Banking() {
  const ready = useReady();
  const [rev, setRev] = useState(0);
  const connections = useApi(() => api.get("/api/v1/banking/connections"), ready, [rev]);
  const mappings = useApi(() => api.get("/api/v1/banking/mappings"), ready, [rev]);
  const accounts = useAccounts(rev);
  const [connecting, setConnecting] = useState(false);
  const [rotating, setRotating] = useState<Connection | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const refresh = () => setRev((r) => r + 1);

  const conns = Array.isArray(connections.data) ? connections.data : [];
  const maps = Array.isArray(mappings.data) ? mappings.data : [];
  const acctById = useMemo(() => {
    const m = new Map<number, Account>();
    for (const a of accounts.data?.items ?? []) m.set(a.id, a);
    return m;
  }, [accounts.data]);

  const linked = maps.filter((m) => m.status === "linked");
  const proposed = maps.filter((m) => m.status === "proposed");
  const ignored = maps.filter((m) => m.status === "ignored");

  const act = (p: Promise<unknown>, msg: string, onDone?: () => void) => {
    p.then(() => { toast.success(msg); onDone?.(); refresh(); })
      .catch((e: Error) => toast.error(e.message))
      .finally(() => setBusyId(null));
  };

  const discover = (c: Connection) => {
    setBusyId(c.id);
    act(api.post("/api/v1/banking/connections/{cid}/discover", { path: { cid: c.id } }),
      "Account discovery complete");
  };
  const sync = (c: Connection) => {
    setBusyId(c.id);
    api.post("/api/v1/banking/connections/{cid}/sync", { path: { cid: c.id } })
      .then((r) => {
        const results = (r as { results?: Array<{ status: string; inserted: number; error?: string }> }).results ?? [];
        const inserted = results.reduce((s, x) => s + (x.inserted ?? 0), 0);
        const failed = results.filter((x) => x.status === "error" || x.error);
        if (failed.length) {
          toast.warning(`Sync finished with ${failed.length} error${failed.length === 1 ? "" : "s"} — ${failed[0]!.error ?? "see history"}`);
        } else {
          toast.success(`Sync complete — ${inserted} new transaction${inserted === 1 ? "" : "s"}`);
        }
        refresh();
      })
      .catch((e: Error) => toast.error(e.message))
      .finally(() => setBusyId(null));
  };
  const syncBalances = (c: Connection) => {
    setBusyId(c.id);
    act(api.post("/api/v1/banking/connections/{cid}/sync-balances", { path: { cid: c.id } }),
      "Balances refreshed");
  };
  const disconnect = (c: Connection) => {
    setBusyId(c.id);
    act(api.post("/api/v1/banking/connections/{cid}/disconnect", { path: { cid: c.id } }),
      `${c.provider} disconnected`);
  };
  const linkMapping = (m: Mapping, accountId: number | null,
                       create: boolean | "joint") =>
    act(api.post("/api/v1/banking/mappings/{mid}/link", {
      path: { mid: m.id },
      body: create
        ? { create_account: true,
            account_type: create === "joint" ? "joint" : "personal" }
        : { account_id: accountId, create_account: false },
    }), create ? `Created account for ${m.external_label}` : `${m.external_label} linked`);
  const ignoreMapping = (m: Mapping) =>
    act(api.post("/api/v1/banking/mappings/{mid}/ignore", { path: { mid: m.id } }),
      `${m.external_label} ignored`);

  const syncable = (c: Connection) => c.status === "connected" || c.status === "degraded";

  return (
    <div>
      <PageHeader
        title="Banking"
        description="Connect your bank — transactions and balances sync themselves in."
        actions={conns.length > 0
          ? <Badge tone="info">{conns.length} connection{conns.length === 1 ? "" : "s"}</Badge>
          : undefined}
      />

      <DataView
        state={connections}
        empty="Connect your bank"
        emptyDescription={
          <>Link Starling and your transactions, balances and bills sync
          automatically — no more manual entry. Your token is validated
          then stored encrypted in the vault; it's never shown again.</>
        }
        emptyIcon={Building2}
        emptyAction={
          <Button variant="primary" onClick={() => setConnecting(true)}>
            <Landmark /> Connect Starling
          </Button>
        }
        loading={<Skeleton className="h-56 w-full rounded-xl" />}
      >
        {() => (
          <div className="flex flex-col gap-4">
            {/* Connections */}
            <div className="grid gap-3 lg:grid-cols-2">
              {conns.map((c) => (
                <Panel key={c.id} className="flex flex-col gap-3">
                  <div className="flex items-start gap-3">
                    <IconTile icon={Building2} label={c.provider} />
                    <div className="min-w-0 flex-1">
                      <p className="text-[14.5px] font-semibold capitalize">{c.label ?? c.provider}</p>
                      <p className="text-[12px] text-fg-subtle">
                        {c.last_validated_at ? `Verified ${formatRelativeDays(c.last_validated_at.slice(0, 10))}` : "Not yet verified"}
                      </p>
                    </div>
                    <Badge tone={STATUS_TONE[c.status] ?? "neutral"}>
                      {STATUS_COPY[c.status] ?? c.status}
                    </Badge>
                  </div>
                  {c.scopes && (
                    <div className="flex flex-wrap gap-1">
                      {c.scopes.split(/[ ,]+/).filter(Boolean).slice(0, 8).map((s) => (
                        <Badge key={s} tone="neutral" className="!text-[11px]">{s}</Badge>
                      ))}
                      {c.scopes.split(/[ ,]+/).filter(Boolean).length > 8 && (
                        <Badge tone="neutral" className="!text-[11px]">
                          +{c.scopes.split(/[ ,]+/).filter(Boolean).length - 8} more
                        </Badge>
                      )}
                    </div>
                  )}
                  {c.last_error && (
                    <p role="alert" className="rounded-lg bg-negative/10 px-3 py-2 text-[12.5px] text-negative">
                      {c.last_error}
                    </p>
                  )}
                  <div className="flex flex-wrap items-center gap-1.5 border-t border-border pt-3">
                    <Button size="sm" variant="secondary" disabled={!syncable(c) || busyId === c.id}
                      onClick={() => sync(c)} aria-label={`Sync ${c.provider}`}>
                      <RefreshCw className={cn(busyId === c.id && "animate-spin")} /> Sync now
                    </Button>
                    <Button size="sm" variant="ghost" disabled={!syncable(c) || busyId === c.id}
                      onClick={() => discover(c)} aria-label={`Discover accounts on ${c.provider}`}>
                      <Link2 /> Discover
                    </Button>
                    <Button size="sm" variant="ghost" disabled={!syncable(c) || busyId === c.id}
                      onClick={() => syncBalances(c)} aria-label={`Refresh balances on ${c.provider}`}>
                      Balances
                    </Button>
                    <span className="ml-auto flex gap-1">
                      <Button size="sm" variant="ghost" onClick={() => setRotating(c)}
                        aria-label={`Rotate ${c.provider} token`} title="Replace token">
                        <RotateCcwKey />
                      </Button>
                      <Button size="sm" variant="ghost" disabled={busyId === c.id}
                        onClick={() => disconnect(c)} aria-label={`Disconnect ${c.provider}`} title="Disconnect">
                        <Trash2 />
                      </Button>
                    </span>
                  </div>
                </Panel>
              ))}
            </div>

            {/* Proposed mappings */}
            {proposed.length > 0 && (
              <Panel
                title={`Discovered accounts (${proposed.length})`}
                description="Accounts found on your bank — link each to an account here, create one, or ignore it."
                flush
              >
                <ul className="divide-y divide-border">
                  {proposed.map((m) => (
                    <ProposedRow key={m.id} mapping={m}
                      accounts={(accounts.data?.items ?? []).filter((a) => !a.archived)}
                      onLink={linkMapping} onIgnore={ignoreMapping} />
                  ))}
                </ul>
              </Panel>
            )}

            {/* Linked mappings */}
            {linked.length > 0 && (
              <Panel
                title={`Linked accounts (${linked.length})`}
                description="Bank accounts flowing into your ledger."
                flush
              >
                <ul className="divide-y divide-border">
                  {linked.map((m) => {
                    const acct = m.account_id != null ? acctById.get(m.account_id) : undefined;
                    return (
                      <li key={m.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3">
                        <IconTile label={m.external_label} />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-[13.5px] font-medium">{m.external_label}</p>
                          <p className="text-[12px] text-fg-subtle">
                            → {acct?.name ?? "unmapped"}{m.external_kind ? ` · ${m.external_kind}` : ""}
                          </p>
                        </div>
                        {m.balance_pence != null && (
                          <div className="text-right">
                            <Money pence={m.balance_pence} className="text-[13.5px] font-semibold" />
                            <p className="text-[11.5px] text-fg-subtle">
                              {m.balance_at ? formatRelativeDays(m.balance_at.slice(0, 10)) : ""}
                            </p>
                          </div>
                        )}
                        <Badge tone="success"><Check className="size-3" /> linked</Badge>
                      </li>
                    );
                  })}
                </ul>
              </Panel>
            )}

            {/* Sync history per linked account */}
            {linked.length > 0 && (
              <Panel
                title="Sync activity"
                description="Latest runs per linked account — fetched / inserted / deduped."
                flush
              >
                <div className="divide-y divide-border">
                  {linked.map((m) => m.account_id != null && (
                    <SyncHistory key={m.id} accountId={m.account_id} label={m.external_label} rev={rev} />
                  ))}
                </div>
              </Panel>
            )}

            {ignored.length > 0 && (
              <Panel title={`Ignored (${ignored.length})`} flush>
                <ul className="divide-y divide-border">
                  {ignored.map((m) => (
                    <li key={m.id} className="flex items-center gap-3 px-4 py-2.5 text-fg-subtle">
                      <X className="size-3.5" />
                      <span className="text-[13px]">{m.external_label}</span>
                    </li>
                  ))}
                </ul>
              </Panel>
            )}
          </div>
        )}
      </DataView>

      <TokenDialog
        open={connecting}
        title="Connect Starling"
        description="Paste a personal access token from the Starling developer dashboard. It's validated against the bank, then stored encrypted — never shown again."
        submitLabel="Connect"
        onClose={() => setConnecting(false)}
        onSubmit={(token, label) =>
          api.post("/api/v1/banking/connections", {
            body: { provider: "starling", token, label: label || null },
          }).then((c) => {
            toast.success("Starling connected — discovering accounts…");
            setConnecting(false);
            refresh();
            // Auto-discover on the connection just created.
            setTimeout(() => {
              void api.post("/api/v1/banking/connections/{cid}/discover",
                { path: { cid: c.id } })
                .then(refresh).catch(() => {});
            }, 400);
          })
        }
      />
      <TokenDialog
        open={rotating !== null}
        title={`Rotate ${rotating?.provider ?? ""} token`}
        description="Paste the replacement token — it's validated before the old one is released."
        submitLabel="Rotate token"
        onClose={() => setRotating(null)}
        onSubmit={(token) =>
          api.post("/api/v1/banking/connections/{cid}/rotate", {
            path: { cid: rotating!.id },
            body: { provider: rotating!.provider, token },
          }).then(() => { toast.success("Token rotated"); setRotating(null); refresh(); })
        }
      />
    </div>
  );
}

/** A discovered provider account awaiting a decision. */
function ProposedRow({ mapping, accounts, onLink, onIgnore }: {
  mapping: Mapping;
  accounts: Account[];
  onLink: (m: Mapping, accountId: number | null,
           create: boolean | "joint") => void;
  onIgnore: (m: Mapping) => void;
}) {
  const [target, setTarget] = useState("");
  const [kind, setKind] = useState<"personal" | "joint">(
    mapping.external_kind === "space" ? "joint" : "personal");
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
      <IconTile label={mapping.external_label} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13.5px] font-medium">{mapping.external_label}</p>
        <p className="text-[12px] text-fg-subtle">
          {mapping.external_kind === "space" ? "space" : mapping.external_kind}
          {mapping.balance_pence != null && <> · balance <Money pence={mapping.balance_pence} /></>}
        </p>
      </div>
      <Select aria-label={`Link ${mapping.external_label} to`} value={target}
        onChange={(e) => setTarget(e.target.value)}
        wrapperClassName="w-44" sizeVariant="sm">
        <option value="" disabled>Link to…</option>
        {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
      </Select>
      <Select aria-label={`New account type for ${mapping.external_label}`}
        value={kind}
        onChange={(e) => setKind(e.target.value as "personal" | "joint")}
        wrapperClassName="w-32" sizeVariant="sm">
        <option value="personal">Mine</option>
        <option value="joint">Joint</option>
      </Select>
      <div className="flex gap-1.5">
        <Button size="sm" variant="primary" disabled={!target}
          onClick={() => onLink(mapping, Number(target), false)}
          aria-label={`Link ${mapping.external_label}`}>
          <Link2 /> Link
        </Button>
        <Button size="sm" variant="secondary"
          onClick={() => onLink(mapping, null, kind === "joint" ? "joint" : true)}
          aria-label={`Create account for ${mapping.external_label}`}>
          Create account
        </Button>
        <Button size="sm" variant="ghost" onClick={() => onIgnore(mapping)}
          aria-label={`Ignore ${mapping.external_label}`}>
          Ignore
        </Button>
      </div>
    </li>
  );
}

/** Recent sync runs for one account (issue #271). */
function SyncHistory({ accountId, label, rev }: {
  accountId: number; label: string; rev: number;
}) {
  const ready = useReady();
  const runs = useApi(
    () => api.get("/api/v1/sync/accounts/{account_id}/runs", { path: { account_id: accountId } }),
    ready, [rev]);
  const rows = (Array.isArray(runs.data) ? runs.data : []).slice(0, 3);
  if (runs.loading) return <div className="px-4 py-3"><Skeleton className="h-10 w-full" /></div>;
  return (
    <div className="px-4 py-3">
      <p className="mb-1.5 flex items-center gap-2 text-[12.5px] font-medium text-fg-muted">
        <History className="size-3.5" /> {label}
      </p>
      {rows.length === 0 ? (
        <p className="pl-5.5 text-[12px] text-fg-subtle">No sync runs yet — hit Sync now on the connection.</p>
      ) : (
        <ul className="flex flex-col gap-1 pl-5.5">
          {rows.map((r: SyncRun) => (
            <li key={r.id} className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[12.5px]">
              <Badge tone={r.status === "ok" ? "success" : r.status === "error" ? "error" : "info"} className="!text-[11px]">
                {r.status}
              </Badge>
              <span className="text-fg-muted">
                {r.fetched} fetched · {r.inserted} inserted · {r.deduped} deduped
                {r.merge_candidates > 0 && <> · {r.merge_candidates} to review</>}
              </span>
              <span className="ml-auto text-[11.5px] text-fg-subtle">{formatDateTime(r.started_at)}</span>
              {r.error && <p className="w-full text-[11.5px] text-negative">{r.error}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Token entry — masked field, validated server-side before the vault
 * stores it. Never echo back. */
function TokenDialog({ open, title, description, submitLabel, onClose, onSubmit }: {
  open: boolean;
  title: string;
  description: string;
  submitLabel: string;
  onClose: () => void;
  onSubmit: (token: string, label: string) => Promise<void>;
}) {
  const [token, setToken] = useState("");
  const [label, setLabel] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!token.trim()) return;
    setBusy(true);
    onSubmit(token.trim(), label.trim())
      .catch((err: Error) => toast.error(err.message))
      .finally(() => { setBusy(false); setToken(""); });
  };
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}
      title={title} description={description}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-token" disabled={!token.trim() || busy}>
          {busy ? "Validating…" : submitLabel}
        </Button>
      </>}>
      <form id="ff-token" onSubmit={submit} className="grid gap-4">
        <Field label="Personal access token"
          hint="From Starling → Settings → Personal Access Token. Needs account + transaction read scopes.">
          {(id) => (
            <div className="relative">
              <Input id={id} required autoFocus autoComplete="off" spellCheck={false}
                type={show ? "text" : "password"} placeholder="eyJ…"
                value={token} onChange={(e) => setToken(e.target.value)}
                className="pr-10 font-mono text-[12.5px]" />
              <button type="button" onClick={() => setShow((s) => !s)}
                aria-label={show ? "Hide token" : "Show token"}
                className="absolute top-1/2 right-2.5 -translate-y-1/2 text-fg-subtle hover:text-fg">
                {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
              </button>
            </div>
          )}
        </Field>
        <Field label="Label" hint="Optional — e.g. 'Joint account'.">
          {(id) => <Input id={id} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Starling" />}
        </Field>
        <p className="flex items-start gap-2 rounded-lg bg-surface-2 px-3 py-2 text-[12px] text-fg-muted">
          <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-positive" />
          Stored encrypted in the local vault — it never appears in pages, logs, backups or the response after save.
        </p>
      </form>
    </Dialog>
  );
}
