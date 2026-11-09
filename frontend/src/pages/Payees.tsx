/** Payees: the canonical payee list (issue #276). Banks and imports
 * spell the same merchant a dozen ways — renaming here rewrites every
 * editable transaction; renaming onto an existing name merges the two.
 * Reconciled rows stay immutable — skipped counts are reported. */
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Pencil, Search, Tag } from "lucide-react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { useReady } from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Button } from "../components/Button";
import { Dialog } from "../components/Dialog";
import { Field, Input } from "../components/Field";
import { IconTile, PageHeader } from "../components/Display";
import { Skeleton } from "../components/Feedback";
import { cn } from "../lib/cn";

type Payee = components["schemas"]["PayeeOut"];

export function Payees() {
  const ready = useReady();
  const [rev, setRev] = useState(0);
  const [q, setQ] = useState("");
  const payees = useApi(() => api.get("/api/v1/payees"), ready, [rev]);
  const [renaming, setRenaming] = useState<Payee | null>(null);
  const refresh = () => setRev((r) => r + 1);

  const items = useMemo(() => {
    const all = payees.data ?? [];
    const needle = q.trim().toLowerCase();
    return needle ? all.filter((p) => p.payee.toLowerCase().includes(needle)) : all;
  }, [payees.data, q]);

  const mostUsed = useMemo(
    () => Math.max(1, ...(payees.data ?? []).map((p) => p.transaction_count)),
    [payees.data]);

  return (
    <div>
      <PageHeader
        title="Payees"
        description="Every merchant name in your transactions — tidy the variants into clean canonical names."
      />

      <div className="relative mb-4 max-w-sm">
        <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-fg-subtle" />
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search payees…"
          aria-label="Search payees"
          className="pl-9"
        />
      </div>

      <DataView
        state={{ ...payees, data: items }}
        empty={q ? `No payees matching "${q}"` : "No payees yet"}
        emptyIcon={Tag}
        emptyDescription="Payees appear as transactions are recorded or imported."
        loading={<Skeleton className="h-64 w-full rounded-xl" />}
      >
        {(list) => (
          <ul className="divide-y divide-border rounded-xl border border-border bg-surface">
            {list.map((p) => (
              <li key={p.payee}
                className="group flex items-center gap-3 px-4 py-2.5">
                <IconTile label={p.payee} size="sm" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13.5px] font-medium">{p.payee}</p>
                  <div className="mt-0.5 h-1 w-24 overflow-hidden rounded-full bg-surface-2">
                    <div
                      className="h-full rounded-full bg-accent/70"
                      style={{ width: `${Math.max(6, p.transaction_count / mostUsed * 100)}%` }}
                    />
                  </div>
                </div>
                <span className="text-[12.5px] whitespace-nowrap text-fg-subtle">
                  {p.transaction_count} txn{p.transaction_count === 1 ? "" : "s"}
                </span>
                <Button
                  variant="ghost" size="icon-sm"
                  aria-label={`Rename ${p.payee}`}
                  title="Rename or merge"
                  className={cn("opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100")}
                  onClick={() => setRenaming(p)}
                >
                  <Pencil />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </DataView>

      {renaming && (
        <RenameDialog
          payee={renaming}
          existing={payees.data ?? []}
          onClose={() => setRenaming(null)}
          onDone={(renamed, skipped) => {
            setRenaming(null);
            toast.success(
              skipped > 0
                ? `Renamed ${renamed} transaction${renamed === 1 ? "" : "s"} — ${skipped} reconciled kept the old name`
                : `Renamed ${renamed} transaction${renamed === 1 ? "" : "s"}`);
            refresh();
          }}
        />
      )}
    </div>
  );
}

function RenameDialog({ payee, existing, onClose, onDone }: {
  payee: Payee;
  existing: Payee[];
  onClose: () => void;
  onDone: (renamed: number, skipped: number) => void;
}) {
  const [target, setTarget] = useState(payee.payee);
  const [busy, setBusy] = useState(false);
  const trimmed = target.trim();
  const merge = existing.some(
    (p) => p.payee === trimmed && p.payee !== payee.payee);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!trimmed || trimmed === payee.payee) return;
    setBusy(true);
    api.post("/api/v1/payees/rename", {
      body: { source: payee.payee, target: trimmed },
    }).then((r) => onDone(r.renamed, r.skipped))
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}
      title={`Rename “${payee.payee}”`}
      description={merge
        ? `“${trimmed}” already exists — this merges ${payee.transaction_count} transaction${payee.transaction_count === 1 ? "" : "s"} into it.`
        : "Updates every editable transaction with this payee."}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-payee-rename"
          disabled={busy || !trimmed || trimmed === payee.payee}>
          {busy ? "Renaming…" : merge ? "Merge" : "Rename"}
        </Button>
      </>}>
      <form id="ff-payee-rename" onSubmit={submit} className="grid gap-4">
        <Field label="New name">{(_id) => (
          <Input
            required
            aria-label="New payee name"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            list="ff-payee-names"
            autoFocus
          />
        )}</Field>
        <datalist id="ff-payee-names">
          {existing.filter((p) => p.payee !== payee.payee).map((p) => (
            <option key={p.payee} value={p.payee} />
          ))}
        </datalist>
        {merge && (
          <p className="text-[12.5px] text-warning">
            This merge can’t be undone per-transaction — the old name disappears.
          </p>
        )}
      </form>
    </Dialog>
  );
}
