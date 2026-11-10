/** Accounts: net-worth summary, per-account balance cards with
 * verify-balance reconciliation, archive/reopen lifecycle and an
 * add-account flow. */
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  Archive, ArchiveRestore, BadgeCheck, Landmark, Pencil, Plus,
  Receipt,
} from "lucide-react";
import { api } from "../api/client";
import { useApi, useMeProfileId } from "../api/useApi";
import { useAccounts, useReady } from "../api/hooks";
import type { components } from "../api/schema";
import { Panel } from "../components/Panel";
import { Money } from "../components/Money";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Dialog } from "../components/Dialog";
import { Field, Input, Select } from "../components/Field";
import { IconTile, PageHeader, Segmented, Stat } from "../components/Display";
import { DataView } from "../components/DataView";
import { Skeleton } from "../components/Feedback";
import {
  formatDayMonth, formatPence, formatSmartDate, toISODate,
} from "../lib/format";

type Account = components["schemas"]["AccountOut"];
type AccountType = components["schemas"]["AccountType"];
type Profile = components["schemas"]["ProfileOut"];

const TYPES: AccountType[] = [
  "personal", "joint", "bills", "savings", "cash", "credit", "loan",
];
const TYPE_LABEL: Record<AccountType, string> = {
  personal: "Personal", joint: "Joint", bills: "Bills",
  savings: "Savings", cash: "Cash", credit: "Credit", loan: "Loan",
};

type Scope = "all" | "mine" | "shared";

export function Accounts() {
  const ready = useReady();
  const [rev, setRev] = useState(0);
  const [showArchived, setShowArchived] = useState(false);
  const [scope, setScope] = useState<Scope>("all");
  const [adding, setAdding] = useState(false);
  const [verifying, setVerifying] = useState<Account | null>(null);
  const [editing, setEditing] = useState<Account | null>(null);
  const accounts = useAccounts(rev);
  const profiles = useApi(() => api.get("/api/v1/profiles"), ready);
  const me = useMeProfileId();
  const netWorth = useApi(() => api.get("/api/v1/analytics/net-worth"), ready, [rev]);
  const ownerName = (id: number | null) =>
    profiles.data?.items.find((p) => p.id === id)?.display_name ?? null;

  const items = (accounts.data?.items ?? [])
    .filter((a) => showArchived || !a.archived)
    .filter((a) =>
      scope === "all" ||
      (scope === "mine" ? a.owner_profile_id === me : a.owner_profile_id === null));
  const memberCount = (profiles.data?.items ?? []).filter((p) => p.is_active).length;
  const archivedCount = (accounts.data?.items ?? []).filter((a) => a.archived).length;
  const refresh = () => setRev((r) => r + 1);

  const lifecycle = (a: Account) =>
    (a.archived
      ? api.post("/api/v1/accounts/{account_id}/reopen", { path: { account_id: a.id } })
      : api.post("/api/v1/accounts/{account_id}/archive", { path: { account_id: a.id } })
    ).then(() => {
      toast.success(a.archived ? `${a.name} reopened` : `${a.name} archived`);
      refresh();
    }).catch((e: Error) => toast.error(e.message));

  return (
    <div>
      <PageHeader
        title="Accounts"
        description="Balances, account health and reconciliation."
        actions={
          <>
            {memberCount > 1 && (
              <Segmented
                label="Account scope"
                value={scope}
                onChange={setScope}
                options={[
                  { value: "all", label: "All" },
                  { value: "mine", label: "Mine" },
                  { value: "shared", label: "Shared" },
                ]}
              />
            )}
            {archivedCount > 0 && (
              <Button variant="ghost" onClick={() => setShowArchived((s) => !s)}>
                {showArchived ? "Hide archived" : `Show archived (${archivedCount})`}
              </Button>
            )}
            <Button variant="primary" onClick={() => setAdding(true)}><Plus /> Add account</Button>
          </>
        }
      />

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-3">
        <Stat
          className="rounded-xl border border-border bg-surface p-4 shadow-card"
          label="Net worth"
          value={netWorth.data ? <Money pence={netWorth.data.net_worth_pence} /> : "—"}
          hint="accounts plus loan positions"
        />
        <Stat
          className="rounded-xl border border-border bg-surface p-4 shadow-card"
          label="Accounts"
          value={String((accounts.data?.items ?? []).filter((a) => !a.archived).length)}
          hint={archivedCount ? `${archivedCount} archived` : undefined}
        />
        <Stat
          className="hidden rounded-xl border border-border bg-surface p-4 shadow-card lg:block"
          label="Members"
          value={profiles.data ? String(profiles.data.items.length) : "—"}
          hint="on this household"
        />
      </div>

      <DataView
        state={{ ...accounts, data: items }}
        empty="No accounts yet"
        emptyIcon={Landmark}
        emptyDescription="Add your first account to start tracking balances."
        emptyAction={<Button variant="primary" onClick={() => setAdding(true)}><Plus /> Add account</Button>}
        loading={
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2].map((i) => <Skeleton key={i} className="h-40 rounded-xl" />)}
          </div>
        }
      >
        {(list) => (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {list.map((a) => (
              <AccountCard
                key={a.id}
                account={a}
                owner={ownerName(a.owner_profile_id)}
                onVerify={() => setVerifying(a)}
                onEdit={() => setEditing(a)}
                onLifecycle={() => lifecycle(a)}
                rev={rev}
              />
            ))}
          </div>
        )}
      </DataView>

      <AddAccountDialog
        open={adding}
        onOpenChange={setAdding}
        profiles={profiles.data?.items ?? []}
        onSaved={refresh}
      />
      {verifying && (
        <VerifyDialog
          account={verifying}
          onOpenChange={(o) => { if (!o) setVerifying(null); }}
          onSaved={refresh}
        />
      )}
      {editing && (
        <EditAccountDialog
          account={editing}
          profiles={profiles.data?.items ?? []}
          onOpenChange={(o) => { if (!o) setEditing(null); }}
          onSaved={refresh}
        />
      )}
    </div>
  );
}

function AccountCard({ account: a, owner, onVerify, onEdit,
  onLifecycle, rev }: {
  account: Account;
  owner: string | null;
  onVerify: () => void;
  onEdit: () => void;
  onLifecycle: () => void;
  rev: number;
}) {
  const balance = useApi(
    () => api.get("/api/v1/accounts/{account_id}/balance",
      { path: { account_id: a.id } }),
    true,
    [a.id, rev],
  );
  const pence = balance.data && "balance_pence" in balance.data
    ? Number(balance.data.balance_pence) : null;
  const belowFloor = pence !== null && a.min_balance_pence !== null
    && pence < a.min_balance_pence;
  return (
    <Panel className={a.archived ? "opacity-60" : undefined}>
      <div className="flex items-start gap-3">
        <IconTile label={a.type} size="lg" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-[15px] font-semibold">{a.name}</p>
            {a.archived && <Badge tone="neutral">Archived</Badge>}
          </div>
          <p className="text-[12.5px] text-fg-subtle">
            {TYPE_LABEL[a.type]}{owner ? ` · ${owner}` : ""}
            {a.institution_ref ? ` · ${a.institution_ref}` : ""}
          </p>
        </div>
      </div>
      <div className="mt-4 flex items-end justify-between gap-3">
        <div>
          <p className="text-[12px] text-fg-subtle">Balance</p>
          {balance.loading ? (
            <Skeleton className="mt-1 h-7 w-24" />
          ) : (
            <p className={`tnum text-[24px] font-semibold tracking-tight ${belowFloor ? "text-negative" : ""}`}>
              {pence !== null ? formatPence(pence) : "—"}
            </p>
          )}
          {a.min_balance_pence !== null && (
            <p className="mt-0.5 text-[12px] text-fg-subtle">
              Floor {formatPence(a.min_balance_pence)}
              {belowFloor && <span className="font-medium text-negative"> · below</span>}
            </p>
          )}
        </div>
      </div>
      <div className="mt-4 flex items-center gap-1.5 border-t border-border pt-3">
        <Button size="sm" variant="secondary" onClick={onVerify} disabled={a.archived}>
          <BadgeCheck /> Verify
        </Button>
        <Link
          to="/transactions"
          className="inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-[13px] font-medium text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg"
        >
          <Receipt className="size-4" /> Transactions
        </Link>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={onEdit}
          aria-label={`Edit ${a.name}`}>
          <Pencil />
        </Button>
        <Button size="sm" variant="ghost" onClick={onLifecycle}
          aria-label={a.archived ? `Reopen ${a.name}` : `Archive ${a.name}`}>
          {a.archived ? <ArchiveRestore /> : <Archive />}
        </Button>
      </div>
      <p className="mt-2 text-[11.5px] text-fg-subtle">Opened {formatDayMonth(a.opening_date)}</p>
    </Panel>
  );
}

function VerifyDialog({ account, onOpenChange, onSaved }: {
  account: Account;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const [balance, setBalance] = useState("");
  const [date, setDate] = useState(toISODate(new Date()));
  const [result, setResult] = useState<components["schemas"]["BalanceVerificationOut"] | null>(null);
  const [busy, setBusy] = useState(false);
  const valid = /^\d+(\.\d{1,2})?$/.test(balance);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    api.post("/api/v1/accounts/{account_id}/verify-balance", {
      path: { account_id: account.id },
      body: {
        asserted_balance: balance,
        verified_date: date,
        correct: true,
      },
    })
      .then((v) => {
        setResult(v);
        onSaved();
        toast.success(
          v.variance_pence === 0
            ? "Balance verified — no adjustment needed"
            : `Verified with ${formatPence(Math.abs(v.variance_pence))} adjustment`,
        );
      })
      .catch((err: Error) => { toast.error(err.message); setBusy(false); });
  };
  return (
    <Dialog
      open
      onOpenChange={onOpenChange}
      title={`Verify ${account.name}`}
      description="Enter the balance your bank shows. If it differs from the ledger, a correcting transaction is created automatically."
      footer={result ? (
        <Button variant="primary" onClick={() => onOpenChange(false)}>Done</Button>
      ) : (
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" type="submit" form="ff-verify" disabled={!valid || busy}>
            {busy ? "Checking…" : "Verify balance"}
          </Button>
        </>
      )}
    >
      {result ? (
        <div className="grid grid-cols-1 gap-3">
          <div className="grid grid-cols-3 gap-2 text-center">
            <div className="rounded-lg bg-surface-2 p-3">
              <p className="text-[11.5px] text-fg-subtle">Ledger</p>
              <Money pence={result.derived_pence} className="font-semibold" />
            </div>
            <div className="rounded-lg bg-surface-2 p-3">
              <p className="text-[11.5px] text-fg-subtle">Bank says</p>
              <Money pence={result.asserted_pence} className="font-semibold" />
            </div>
            <div className="rounded-lg bg-surface-2 p-3">
              <p className="text-[11.5px] text-fg-subtle">Difference</p>
              <Money pence={result.variance_pence} signed tone="variance" className="font-semibold" />
            </div>
          </div>
          <p className="text-[12.5px] text-fg-subtle">
            {result.variance_pence === 0
              ? "Ledger and bank agree — nothing to fix."
              : "A correcting transaction was added for the difference."}
          </p>
        </div>
      ) : (
        <form id="ff-verify" onSubmit={submit} className="grid gap-4">
          <Field label="Balance shown by bank (£)">
            {(id) => <Input id={id} autoFocus inputMode="decimal" placeholder="0.00" value={balance} onChange={(e) => setBalance(e.target.value.trim())} />}
          </Field>
          <Field label="As of">
            {(id) => <Input id={id} type="date" value={date} onChange={(e) => setDate(e.target.value)} />}
          </Field>
        </form>
      )}
    </Dialog>
  );
}

function AddAccountDialog({ open, onOpenChange, profiles, onSaved }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  profiles: Profile[];
  onSaved: () => void;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState<AccountType>("personal");
  const [balance, setBalance] = useState("0");
  const [date, setDate] = useState(toISODate(new Date()));
  const [ownerId, setOwnerId] = useState("");
  const [busy, setBusy] = useState(false);
  const valid = name.trim() && /^-?\d+(\.\d{1,2})?$/.test(balance);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    api.post("/api/v1/accounts", {
      body: {
        name: name.trim(),
        type,
        opening_balance: balance,
        opening_date: date,
        owner_profile_id: ownerId ? Number(ownerId) : null,
      },
    })
      .then(() => {
        toast.success(`Added ${name.trim()}`);
        setName(""); setBalance("0");
        onSaved();
        onOpenChange(false);
      })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Add account"
      description="A new ledger account — current, savings, cash or credit."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" type="submit" form="ff-add-account" disabled={!valid || busy}>
            {busy ? "Adding…" : "Add account"}
          </Button>
        </>
      }
    >
      <form id="ff-add-account" onSubmit={submit} className="grid gap-4">
        <Field label="Name">
          {(id) => <Input id={id} autoFocus placeholder="e.g. Joint bills" value={name} onChange={(e) => setName(e.target.value)} />}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Type">
            {(id) => (
              <Select id={id} value={type} onChange={(e) => setType(e.target.value as AccountType)}>
                {TYPES.map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Owner" hint="Optional — for per-member accounts.">
            {(id) => (
              <Select id={id} value={ownerId} onChange={(e) => setOwnerId(e.target.value)}>
                <option value="">Shared</option>
                {profiles.map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}
              </Select>
            )}
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Opening balance (£)">
            {(id) => <Input id={id} inputMode="decimal" value={balance} onChange={(e) => setBalance(e.target.value.trim())} />}
          </Field>
          <Field label="As of">
            {(id) => <Input id={id} type="date" value={date} onChange={(e) => setDate(e.target.value)} />}
          </Field>
        </div>
        <p className="text-[12.5px] text-fg-subtle">The opening balance is recorded as of the date above; transactions carry it forward from there.</p>
      </form>
    </Dialog>
  );
}

function EditAccountDialog({ account, profiles, onOpenChange, onSaved }: {
  account: Account;
  profiles: Profile[];
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(account.name);
  const [ownerId, setOwnerId] = useState(
    account.owner_profile_id ? String(account.owner_profile_id) : "");
  const [busy, setBusy] = useState(false);
  const valid = name.trim().length > 0;
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    api.patch("/api/v1/accounts/{account_id}", {
      path: { account_id: account.id },
      body: {
        name: name.trim(),
        owner_profile_id: ownerId ? Number(ownerId) : null,
      },
    })
      .then(() => {
        toast.success(`${name.trim()} updated`);
        onSaved();
        onOpenChange(false);
      })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };
  return (
    <Dialog
      open
      onOpenChange={onOpenChange}
      title={`Edit ${account.name}`}
      description="Rename the account or reassign it to a member — shared stays visible to everyone."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" type="submit" form="ff-edit-account" disabled={!valid || busy}>
            {busy ? "Saving…" : "Save changes"}
          </Button>
        </>
      }
    >
      <form id="ff-edit-account" onSubmit={submit} className="grid gap-4">
        <Field label="Name">
          {(id) => <Input id={id} autoFocus value={name} onChange={(e) => setName(e.target.value)} />}
        </Field>
        <Field label="Owner" hint="Shared accounts are visible to the whole household.">
          {(id) => (
            <Select id={id} value={ownerId} onChange={(e) => setOwnerId(e.target.value)}>
              <option value="">Shared</option>
              {profiles.map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}
            </Select>
          )}
        </Field>
      </form>
    </Dialog>
  );
}
