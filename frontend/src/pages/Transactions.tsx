/** Transaction review (issue #124) + global search/saved filters
 * (issue #126): payee search, status/category filters with named saved
 * sets, inline payee/category edit, bulk select + bulk-categorise,
 * status badges, windowed rows, the expected↔actual match-confirm
 * queue, and a reason-enforced correction flow for reconciled rows —
 * never a silent edit. */
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  Bookmark, Check, Download, FilterX, Lock, Pencil, Plus, Receipt,
  Scissors, StickyNote, Upload, X,
} from "lucide-react";
import { api } from "../api/client";
import { useApi, useHouseholds } from "../api/useApi";
import { useAccounts, useCategories } from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Badge, type BadgeTone } from "../components/Badge";
import { Money } from "../components/Money";
import { Button } from "../components/Button";
import { Panel } from "../components/Panel";
import { Dialog } from "../components/Dialog";
import { Checkbox, Field, Input, SearchInput, Select, Textarea } from "../components/Field";
import { IconTile, PageHeader } from "../components/Display";
import { Skeleton } from "../components/Feedback";
import { cn } from "../lib/cn";
import { formatDate, formatSmartDate } from "../lib/format";
import { useShell } from "../shell/ShellContext";

type Txn = components["schemas"]["TransactionOut"];
type Category = components["schemas"]["CategoryOut"];
type Status = components["schemas"]["TransactionStatus"];
type Recon = components["schemas"]["ReconciliationOut"];

const STATUSES: Status[] = ["pending", "cleared", "reconciled", "void"];
const STATUS_TONE: Record<Status, BadgeTone> = {
  pending: "warning", cleared: "info", reconciled: "success", void: "error",
};
const SAVED_KEY = "ff.txnFilters";
const ROW_PX = 56;
const PAGE_SIZE = 200;
const OVERSCAN = 8;

interface Filters { q: string; status: string; category_id: string }
const EMPTY: Filters = { q: "", status: "", category_id: "" };

function loadSaved(): Record<string, Filters> {
  try {
    return JSON.parse(localStorage.getItem(SAVED_KEY) ?? "{}");
  } catch { return {}; }
}

/** Windowed row range for a scrollable container — renders only the
 * visible slice plus overscan, keeping large ledgers fast (#124).
 * Small lists render in full: windowing only pays off beyond ~80 rows. */
function useWindowed(count: number) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(640);
  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    setScrollTop(el.scrollTop);
    setHeight(el.clientHeight);
  };
  if (count <= 80) return { ref, onScroll, first: 0, last: count };
  const first = Math.max(0, Math.floor(scrollTop / ROW_PX) - OVERSCAN);
  const last = Math.min(
    count, Math.ceil((scrollTop + height) / ROW_PX) + OVERSCAN);
  return { ref, onScroll, first, last };
}

export function Transactions() {
  const households = useHouseholds();
  const ready = households.data !== null;
  const { dataVersion, openQuickAdd } = useShell();
  const categories = useCategories();
  const accounts = useAccounts();
  const [filters, setFiltersRaw] = useState<Filters>(EMPTY);
  const [saved, setSaved] = useState<Record<string, Filters>>(loadSaved);
  const [saving, setSaving] = useState(false);
  const [rev, setRev] = useState(0);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [bulkCat, setBulkCat] = useState("");
  const [correcting, setCorrecting] = useState<Txn | null>(null);
  const [noting, setNoting] = useState<Txn | null>(null);
  const [splitting, setSplitting] = useState<Txn | null>(null);
  const [importing, setImporting] = useState(false);
  const [page, setPage] = useState(1);
  const [loaded, setLoaded] = useState<Txn[]>([]);
  const state = useApi(
    () => api.get("/api/v1/transactions", {
      query: {
        q: filters.q || undefined,
        status: (filters.status || undefined) as Status | undefined,
        category_id: filters.category_id
          ? Number(filters.category_id) : undefined,
        page,
        size: PAGE_SIZE,
      },
    }).then((response) => ({ ...response, requestedPage: page })),
    ready,
    [filters, rev, dataVersion, page],
  );
  useEffect(() => {
    if (!state.data || state.data.requestedPage !== page) return;
    const next = state.data.items;
    setLoaded((prev) => (page === 1 ? next : [...prev, ...next]));
  }, [state.data]);
  useEffect(() => { setPage(1); }, [dataVersion]);
  const queue = useApi(
    () => api.get("/api/v1/reconcile/queue"), ready, [rev]);
  const items = loaded;
  const total = state.data?.total ?? 0;
  const hasMore = items.length < total;
  const win = useWindowed(items.length);
  const cats = categories.data?.items ?? [];
  const multiAccount = (accounts.data?.items.length ?? 0) > 1;
  const accountName = (id: number) =>
    accounts.data?.items.find((a) => a.id === id)?.name ?? "";
  const active = filters.q || filters.status || filters.category_id;

  const refresh = () => { setPage(1); setRev((r) => r + 1); };
  const setFilters = (f: Filters) => { setPage(1); setFiltersRaw(f); };
  /** Inline edits patch the row in place — no refetch, scroll kept. */
  const patch = (id: number, body: object) =>
    api.patch("/api/v1/transactions/{txn_id}",
      { path: { txn_id: id }, body })
      .then((updated) => {
        if (updated && typeof updated === "object" && "id" in updated) {
          setLoaded((rows) => rows.map((r) => (r.id === id ? { ...r, ...body } : r)));
        } else refresh();
      })
      .catch((e: Error) => toast.error(e.message));
  const bulk = () => {
    if (!bulkCat || selected.size === 0) return;
    const n = selected.size;
    const ids = new Set(selected);
    const categoryId = Number(bulkCat);
    void api.post("/api/v1/transactions/bulk", {
      body: { ids: [...ids], category_id: categoryId },
    }).then(() => {
      setSelected(new Set());
      setBulkCat("");
      setLoaded((rows) => rows.map((r) => (ids.has(r.id) ? { ...r, category_id: categoryId } : r)));
      toast.success(`Categorised ${n} transaction${n === 1 ? "" : "s"}`);
    }).catch((e: Error) => toast.error(e.message));
  };
  const reconAct = (rid: number, action: "confirm" | "dismiss") =>
    (action === "confirm"
      ? api.post("/api/v1/reconcile/{rid}/confirm", { path: { rid } })
      : api.post("/api/v1/reconcile/{rid}/dismiss", { path: { rid } })
    ).then(() => {
      refresh();
      toast.success(action === "confirm" ? "Match confirmed" : "Match dismissed");
    }).catch((e: Error) => toast.error(e.message));
  const toggle = (id: number) => {
    const s = new Set(selected);
    if (s.has(id)) s.delete(id); else s.add(id);
    setSelected(s);
  };
  const selectable = items.filter((t) => t.status !== "void");
  const allSelected = selectable.length > 0 && selectable.every((t) => selected.has(t.id));

  return (
    <div>
      <PageHeader
        title="Transactions"
        description={state.data ? `${total} transaction${total === 1 ? "" : "s"}${active ? " matching filters" : ""}` : "Search, categorise and reconcile"}
        actions={
          <>
            <a
              href="./api/v1/transactions/export.csv"
              className="inline-flex h-9 items-center gap-2 rounded-lg border border-border bg-surface px-3.5 text-sm font-medium hover:bg-surface-2 [&_svg]:size-4"
            >
              <Download /> Export
            </a>
            <Button variant="secondary" onClick={() => setImporting(true)}>
              <Upload /> Import
            </Button>
            <Button variant="primary" onClick={openQuickAdd}><Plus /> Add</Button>
          </>
        }
      />

      {(queue.data?.length ?? 0) > 0 && (
        <Panel
          title={`Expected matches (${queue.data?.length ?? 0})`}
          description="Scheduled movements that look like they've landed — confirm to reconcile."
          flush
          className="mb-4"
          aria-label="Reconciliation queue"
        >
          <ul className="divide-y divide-border">
            {queue.data?.map((r: Recon) => (
              <li key={r.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
                <IconTile label={r.expected_label ?? "transfer"} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13.5px] font-medium">{r.expected_label ?? "—"}</p>
                  <p className="text-[12px] text-fg-subtle">
                    Expected {r.expected_date ? formatSmartDate(r.expected_date) : "—"}
                    {r.amount_drift_pence ? <> · drift <Money pence={r.amount_drift_pence} /></> : null}
                  </p>
                </div>
                {r.expected_amount_pence != null && (
                  <Money pence={r.expected_amount_pence} signed tone="auto" className="font-medium" />
                )}
                <Badge tone="accent">{r.score}% match</Badge>
                <div className="flex gap-1.5">
                  <Button size="sm" variant="primary" onClick={() => reconAct(r.id, "confirm")}>
                    <Check /> Confirm
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => reconAct(r.id, "dismiss")}>
                    Dismiss
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <Panel flush>
        <form
          onSubmit={(e) => e.preventDefault()}
          aria-label="Transaction filters"
          className="flex flex-wrap items-center gap-2 border-b border-border p-3"
        >
          <SearchInput
            placeholder="Search payee…"
            aria-label="Search payee"
            value={filters.q}
            wrapperClassName="w-full sm:w-64"
            onChange={(e) => setFilters({ ...filters, q: e.target.value })}
          />
          <Select
            aria-label="Status"
            value={filters.status}
            wrapperClassName="w-[calc(50%-4px)] sm:w-36"
            onChange={(e) => setFilters({ ...filters, status: e.target.value })}
          >
            <option value="">All statuses</option>
            {STATUSES.map((s) => <option key={s} value={s}>{s[0]!.toUpperCase() + s.slice(1)}</option>)}
          </Select>
          <Select
            aria-label="Category"
            value={filters.category_id}
            wrapperClassName="w-[calc(50%-4px)] sm:w-48"
            onChange={(e) => setFilters({ ...filters, category_id: e.target.value })}
          >
            <option value="">All categories</option>
            {cats.map((c: Category) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </Select>
          <div className="flex items-center gap-2 sm:ml-auto">
            {Object.keys(saved).length > 0 && (
              <Select
                aria-label="Saved filters"
                value=""
                wrapperClassName="w-40"
                onChange={(e) => {
                  const f = saved[e.target.value];
                  if (f) setFilters(f);
                }}
              >
                <option value="">Saved filters…</option>
                {Object.keys(saved).map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </Select>
            )}
            {active && (
              <Button variant="ghost" onClick={() => setFilters(EMPTY)}>
                <FilterX /> Clear
              </Button>
            )}
            <Button variant="secondary" onClick={() => setSaving(true)} disabled={!active}>
              <Bookmark /> Save filter
            </Button>
          </div>
        </form>

        {selected.size > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-b border-border bg-accent-soft px-3 py-2.5">
            <span className="text-[13px] font-medium text-fg">{selected.size} selected</span>
            <Select
              aria-label="Bulk category"
              value={bulkCat}
              sizeVariant="sm"
              wrapperClassName="w-48"
              onChange={(e) => setBulkCat(e.target.value)}
            >
              <option value="">Categorise as…</option>
              {cats.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </Select>
            <Button size="sm" variant="primary" onClick={bulk} disabled={!bulkCat}>Apply to selected</Button>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
              <X /> Clear selection
            </Button>
          </div>
        )}

        <DataView
          state={{
            loading: state.loading && page === 1,
            retrying: state.retrying,
            error: state.error,
            data: state.data || items.length ? items : null,
            refetch: state.refetch,
          }}
          empty={active ? "No transactions match these filters" : "No transactions recorded"}
          emptyDescription={active ? "Try a different search or clear the filters." : "Add one manually or connect a bank feed."}
          emptyIcon={Receipt}
          emptyAction={active
            ? <Button variant="secondary" onClick={() => setFilters(EMPTY)}><FilterX /> Clear filters</Button>
            : <Button variant="primary" onClick={openQuickAdd}><Plus /> Add transaction</Button>}
          loading={<div className="space-y-3 p-4">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-9 w-full" />)}</div>}
        >
          {(rows) => (
            <div
              ref={win.ref}
              onScroll={win.onScroll}
              className="ff-table-window scrollbar-thin max-h-[calc(100dvh-300px)] min-h-[320px] overflow-auto"
              role="region"
              aria-label="Transaction list"
              tabIndex={0}
            >
              <table className="ff-table w-full border-collapse text-[13.5px]">
                <thead className="sticky top-0 z-[1] bg-surface text-left text-[12px] text-fg-subtle shadow-[0_1px_0_var(--border)]">
                  <tr className="h-10">
                    <th className="w-10 pl-4">
                      <button
                        type="button"
                        className="text-[11px] font-medium text-fg-subtle underline-offset-2 hover:text-fg hover:underline"
                        onClick={() => setSelected(allSelected ? new Set() : new Set(selectable.map((t) => t.id)))}
                      >
                        {allSelected ? "None" : "All"}
                      </button>
                    </th>
                    <th className="hidden w-28 px-3 font-medium md:table-cell">Date</th>
                    <th className="px-3 font-medium">Payee</th>
                    <th className="hidden w-56 px-3 font-medium lg:table-cell">Category</th>
                    <th className="hidden w-36 px-3 font-medium sm:table-cell">Status</th>
                    <th className="w-32 px-3 pr-4 text-right font-medium">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {win.first > 0 && (
                    <tr aria-hidden="true">
                      <td colSpan={6} style={{ height: win.first * ROW_PX, padding: 0, border: 0 }} />
                    </tr>
                  )}
                  {rows.slice(win.first, win.last).map((t: Txn) => {
                    const locked = t.status === "reconciled" || t.status === "void";
                    const cat = categories.byId.get(t.category_id ?? -1);
                    return (
                      <tr
                        key={t.id}
                        style={{ height: ROW_PX }}
                        className={cn(
                          "border-b border-border hover:bg-surface-2/60",
                          selected.has(t.id) && "bg-accent-soft/60",
                          t.status === "void" && "opacity-55",
                        )}
                      >
                        <td className="pl-4">
                          <Checkbox
                            aria-label={`Select ${t.payee} ${formatDate(t.date)}`}
                            checked={selected.has(t.id)}
                            onChange={() => toggle(t.id)}
                          />
                        </td>
                        <td className="tnum hidden px-3 whitespace-nowrap text-fg-muted md:table-cell">{formatSmartDate(t.date)}</td>
                        <td className="max-w-0 px-3">
                          <div className="flex min-w-0 items-center gap-2.5">
                            <IconTile label={cat?.name ?? t.payee} size="sm" />
                            <div className="min-w-0 flex-1">
                              {locked ? (
                                <span className={cn("block truncate font-medium", t.status === "void" && "line-through")}>{t.payee}</span>
                              ) : (
                                <InlineText
                                  value={t.payee}
                                  label={`Edit payee for ${t.payee}`}
                                  onSave={(v) => patch(t.id, { payee: v })}
                                />
                              )}
                              <p className="truncate text-[12px] text-fg-subtle">
                                <span className="md:hidden">{formatSmartDate(t.date)}</span>
                                <span className="md:hidden lg:hidden">{cat ? ` · ${cat.name}` : ""}</span>
                                {multiAccount && <span className="hidden md:inline">{accountName(t.account_id)}</span>}
                                {t.notes && (
                                  <StickyNote
                                    className="ml-1 inline size-3 align-[-1px] text-fg-subtle"
                                    aria-label={`Note: ${t.notes}`}
                                  />
                                )}
                              </p>
                            </div>
                          </div>
                        </td>
                        <td className="hidden px-3 lg:table-cell">
                          {locked ? (
                            <span className="text-fg-muted">{cat?.name ?? "—"}</span>
                          ) : (
                            <Select
                              aria-label={`Category for ${t.payee}`}
                              value={t.category_id ?? ""}
                              sizeVariant="sm"
                              className={cn("border-transparent bg-transparent hover:border-border", !t.category_id && "text-fg-subtle")}
                              onChange={(e) =>
                                void patch(t.id, {
                                  category_id: e.target.value ? Number(e.target.value) : null,
                                })}
                            >
                              <option value="">Uncategorised</option>
                              {cats.map((c) => (
                                <option key={c.id} value={c.id}>{c.name}</option>
                              ))}
                            </Select>
                          )}
                        </td>
                        <td className="hidden px-3 sm:table-cell">
                          <div className="flex items-center gap-1">
                            <Badge tone={STATUS_TONE[t.status]}>{t.status}</Badge>
                            {t.splits.length > 0 && (
                              <Badge tone="info">split ×{t.splits.length}</Badge>
                            )}
                            {!locked && t.status !== "void" && (
                              <Button
                                variant="ghost"
                                size="icon-sm"
                                aria-label={`Note for ${t.payee}`}
                                title={t.notes ? "Edit note" : "Add a note"}
                                onClick={() => setNoting(t)}
                              >
                                <StickyNote />
                              </Button>
                            )}
                            {!locked && t.status !== "void" &&
                             t.status !== "reconciled" && (
                              <Button
                                variant="ghost"
                                size="icon-sm"
                                aria-label={`Split ${t.payee}`}
                                title={t.splits.length ? "Edit split" : "Split across categories"}
                                onClick={() => setSplitting(t)}
                              >
                                <Scissors />
                              </Button>
                            )}
                            {t.status === "reconciled" && (
                              <Button
                                variant="ghost"
                                size="icon-sm"
                                aria-label={`Correct ${t.payee}`}
                                title="Issue a correction"
                                onClick={() => setCorrecting(t)}
                              >
                                <Pencil />
                              </Button>
                            )}
                          </div>
                        </td>
                        <td className="px-3 pr-4 text-right">
                          <Money pence={t.amount_pence} signed tone="auto" className="font-medium" />
                        </td>
                      </tr>
                    );
                  })}
                  {win.last < rows.length && (
                    <tr aria-hidden="true">
                      <td colSpan={6} style={{ height: (rows.length - win.last) * ROW_PX, padding: 0, border: 0 }} />
                    </tr>
                  )}
                </tbody>
              </table>
              {hasMore && (
                <div className="flex items-center justify-center gap-3 border-t border-border p-3 text-[12.5px] text-fg-subtle">
                  Showing {items.length} of {total}
                  <Button size="sm" variant="secondary" disabled={state.loading || state.retrying || !!state.error} onClick={() => setPage((p) => p + 1)}>
                    {state.loading || state.retrying ? "Loading…" : "Load more"}
                  </Button>
                </div>
              )}
            </div>
          )}
        </DataView>
      </Panel>

      {correcting && (
        <CorrectionForm
          txn={correcting}
          categories={cats}
          onDone={() => { setCorrecting(null); refresh(); toast.success("Correction issued"); }}
          onCancel={() => setCorrecting(null)}
        />
      )}
      {splitting && (
        <SplitDialog
          txn={splitting}
          categories={cats}
          onClose={() => setSplitting(null)}
          onSaved={(splits) => {
            setSplitting(null);
            setLoaded((prev) => prev.map((r) =>
              r.id === splitting.id ? { ...r, splits } : r));
            toast.success(splits.length ? "Split saved" : "Split cleared");
          }}
        />
      )}
      {noting && (
        <NoteDialog
          txn={noting}
          onClose={() => setNoting(null)}
          onSaved={(notes) => {
            setNoting(null);
            setLoaded((prev) => prev.map((r) =>
              r.id === noting.id ? { ...r, notes } : r));
            toast.success(notes ? "Note saved" : "Note cleared");
          }}
        />
      )}
      {importing && (
        <ImportDialog
          accounts={accounts.data?.items ?? []}
          onClose={() => setImporting(false)}
          onDone={() => { setImporting(false); refresh(); }}
        />
      )}
      <SaveFilterDialog
        open={saving}
        onOpenChange={setSaving}
        onSave={(name) => {
          const next = { ...saved, [name]: filters };
          setSaved(next);
          localStorage.setItem(SAVED_KEY, JSON.stringify(next));
          toast.success(`Saved “${name}”`);
        }}
      />
    </div>
  );
}

function SaveFilterDialog({ open, onOpenChange, onSave }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSave: (name: string) => void;
}) {
  const [name, setName] = useState("");
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => { onOpenChange(o); if (!o) setName(""); }}
      title="Save filter"
      description="Give these filters a name to reuse them later."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" type="submit" form="ff-save-filter" disabled={!name.trim()}>Save</Button>
        </>
      }
    >
      <form
        id="ff-save-filter"
        onSubmit={(e) => {
          e.preventDefault();
          if (!name.trim()) return;
          onSave(name.trim());
          setName("");
          onOpenChange(false);
        }}
      >
        <Field label="Name">
          {(id) => <Input id={id} autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Uncategorised this month" />}
        </Field>
      </form>
    </Dialog>
  );
}

/** Reason-enforced correction form for a reconciled transaction —
 * supersedes, never edits (issue #124). */
function CorrectionForm({ txn, categories, onDone, onCancel }: {
  txn: Txn;
  categories: Category[];
  onDone: () => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const [payee, setPayee] = useState(txn.payee);
  const [categoryId, setCategoryId] = useState(String(txn.category_id ?? ""));
  const [notes, setNotes] = useState(txn.notes ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    api.post("/api/v1/transactions/{txn_id}/correct", {
      path: { txn_id: txn.id },
      body: {
        reason,
        payee: payee !== txn.payee ? payee : undefined,
        category_id: categoryId !== String(txn.category_id ?? "")
          ? (categoryId ? Number(categoryId) : null)
          : undefined,
        notes: notes !== (txn.notes ?? "") ? notes : undefined,
      },
    }).then(onDone).catch((err) => {
      setBusy(false);
      setError(err instanceof Error ? err.message : "Correction failed");
    });
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => { if (!o) onCancel(); }}
      title="Correct reconciled transaction"
      description="The original is voided and a corrected entry re-enters review — history is never edited."
      footer={
        <>
          <Button variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button variant="primary" type="submit" form="ff-correction" disabled={busy || !reason.trim()}>
            {busy ? "Correcting…" : "Issue correction"}
          </Button>
        </>
      }
    >
      <form id="ff-correction" aria-label={`Correction for ${txn.payee}`} onSubmit={submit} className="grid gap-4">
        <div className="flex items-center gap-3 rounded-xl border border-border bg-surface-2 p-3">
          <Lock className="size-4 text-fg-subtle" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13.5px] font-medium">{txn.payee}</p>
            <p className="text-[12px] text-fg-subtle">{formatSmartDate(txn.date)}</p>
          </div>
          <Money pence={txn.amount_pence} signed tone="auto" className="font-medium" />
        </div>
        <Field label="Reason (required)" hint="Recorded in the audit log.">
          {(id) => <Input id={id} required aria-label="Correction reason" value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />}
        </Field>
        <Field label="Payee">
          {(id) => <Input id={id} aria-label="Corrected payee" value={payee} onChange={(e) => setPayee(e.target.value)} />}
        </Field>
        <Field label="Category">
          {(id) => (
            <Select id={id} aria-label="Corrected category" value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
              <option value="">Uncategorised</option>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Notes">
          {(id) => <Input id={id} aria-label="Corrected notes" value={notes} onChange={(e) => setNotes(e.target.value)} />}
        </Field>
        {error && <p role="alert" className="text-[13px] text-negative">{error}</p>}
      </form>
    </Dialog>
  );
}

/** Click-to-edit inline text field (payee). */
function InlineText({ value, label, onSave }: {
  value: string;
  label: string;
  onSave: (v: string) => Promise<unknown>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  if (!editing) {
    return (
      <button
        type="button"
        className="ff-inline-edit block max-w-full truncate rounded text-left font-medium hover:text-accent"
        aria-label={label}
        onClick={() => { setDraft(value); setEditing(true); }}
      >
        {value}
      </button>
    );
  }
  return (
    <Input
      autoFocus
      sizeVariant="sm"
      aria-label={label}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        setEditing(false);
        if (draft !== value && draft.trim()) void onSave(draft.trim());
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") { setDraft(value); setEditing(false); }
      }}
    />
  );
}

/** Free-text note on an editable transaction (issue #282) — a sticky
 * annotation ("paid with the joint card", "split with Sam"), patched
 * like any other field; reconciled rows keep their notes read-only. */
function NoteDialog({ txn, onClose, onSaved }: {
  txn: Txn;
  onClose: () => void;
  onSaved: (notes: string | null) => void;
}) {
  const [notes, setNotes] = useState(txn.notes ?? "");
  const [busy, setBusy] = useState(false);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    const value = notes.trim();
    api.patch("/api/v1/transactions/{txn_id}", {
      path: { txn_id: txn.id },
      body: { notes: value || null },
    }).then(() => onSaved(value || null))
      .catch((err: Error) => { toast.error(err.message); setBusy(false); });
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}
      title={`Note — ${txn.payee}`}
      description={<>
        {formatSmartDate(txn.date)} · <Money pence={txn.amount_pence} signed tone="auto" />
      </>}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-note" disabled={busy}>
          {busy ? "Saving…" : "Save note"}
        </Button>
      </>}>
      <form id="ff-note" onSubmit={submit} className="grid gap-4">
        <Field label="Note">{(id) => (
          <Textarea
            id={id}
            aria-label="Transaction note"
            rows={3}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="e.g. paid with the joint card — split with Sam"
            autoFocus
          />
        )}</Field>
      </form>
    </Dialog>
  );
}

/** Split a transaction across categories (issue #289). Legs are
 * entered as positive magnitudes; the parent's sign is applied on
 * save — an expense splits into expense legs. */
function SplitDialog({ txn, categories, onClose, onSaved }: {
  txn: Txn;
  categories: Category[];
  onClose: () => void;
  onSaved: (splits: Txn["splits"]) => void;
}) {
  type Leg = { category_id: string; amount: string };
  const seed: Leg[] = txn.splits.map((s) => ({
    category_id: String(s.category_id),
    amount: (Math.abs(s.amount_pence) / 100).toString(),
  }));
  const [legs, setLegs] = useState<Leg[]>(
    seed.length ? seed : [
      { category_id: "", amount: "" },
      { category_id: "", amount: "" },
    ]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sign = txn.amount_pence >= 0 ? 1 : -1;
  const entered = legs.map((l) =>
    Math.round(Number.parseFloat(l.amount || "0") * 100));
  const total = entered.reduce((a, b) => a + b, 0);
  const remaining = Math.abs(txn.amount_pence) - total;
  const valid = legs.length >= 2
    && legs.every((l, i) => l.category_id !== ""
      && (entered[i] ?? 0) > 0)
    && remaining === 0;

  const setLeg = (i: number, patch: Partial<Leg>) =>
    setLegs((prev) => prev.map((l, j) => j === i ? { ...l, ...patch } : l));

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    api.put("/api/v1/transactions/{txn_id}/splits", {
      path: { txn_id: txn.id },
      body: {
        splits: legs.map((l, i) => ({
          category_id: Number(l.category_id),
          amount_pence: sign * (entered[i] ?? 0),
        })),
      },
    }).then((r) => onSaved(r.splits)).catch((err) => {
      setBusy(false);
      setError(err instanceof Error ? err.message : "Split failed");
    });
  };

  const clearAll = () => {
    setBusy(true);
    api.put("/api/v1/transactions/{txn_id}/splits", {
      path: { txn_id: txn.id }, body: { splits: [] },
    }).then((r) => onSaved(r.splits)).catch((err) => {
      setBusy(false);
      setError(err instanceof Error ? err.message : "Clear failed");
    });
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}
      title={`Split — ${txn.payee}`}
      description={<>
        {formatSmartDate(txn.date)} · <Money pence={txn.amount_pence} signed tone="auto" />
      </>}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        {txn.splits.length > 0 && (
          <Button variant="ghost" onClick={clearAll} disabled={busy}>
            Clear split
          </Button>
        )}
        <Button variant="primary" type="submit" form="ff-split"
          disabled={busy || !valid}>
          {busy ? "Saving…" : "Save split"}
        </Button>
      </>}>
      <form id="ff-split" onSubmit={submit} className="grid gap-3">
        {legs.map((leg, i) => (
          <div key={i} className="flex items-center gap-2">
            <Select
              aria-label={`Split ${i + 1} category`}
              value={leg.category_id}
              onChange={(e) => setLeg(i, { category_id: e.target.value })}
            >
              <option value="">Category…</option>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </Select>
            <Input
              aria-label={`Split ${i + 1} amount`}
              inputMode="decimal"
              placeholder="0.00"
              value={leg.amount}
              onChange={(e) => setLeg(i, { amount: e.target.value })}
            />
            <Button
              type="button" variant="ghost" size="icon-sm"
              aria-label={`Remove split ${i + 1}`}
              onClick={() =>
                setLegs((prev) => prev.filter((_, j) => j !== i))}
            >
              <X />
            </Button>
          </div>
        ))}
        <div className="flex items-center justify-between">
          <Button
            type="button" variant="secondary" size="sm"
            onClick={() =>
              setLegs((prev) => [...prev,
              { category_id: "", amount: "" }])}
          >
            <Plus /> Add leg
          </Button>
          <p className={cn("text-[12.5px]",
            remaining === 0 ? "text-positive" : "text-fg-subtle")}>
            {remaining === 0
              ? "Balances"
              : <>Remaining <Money pence={sign * remaining} signed /></>}
          </p>
        </div>
        {error && <p role="alert" className="text-[13px] text-negative">{error}</p>}
      </form>
    </Dialog>
  );
}

/** Statement file import wizard (issue #288): pick file + account →
 * column-map (CSV) → review parsed rows with duplicate flags →
 * commit. OFX/QIF need no mapping. */
function ImportDialog({ accounts, onClose, onDone }: {
  accounts: components["schemas"]["AccountOut"][];
  onClose: () => void;
  onDone: () => void;
}) {
  type Row = components["schemas"]["StatementImportRowOut"];
  type Preview = components["schemas"]["StatementImportPreviewOut"];
  const [file, setFile] = useState<File | null>(null);
  const [accountId, setAccountId] = useState("");
  const [cols, setCols] = useState({ date: "Date", payee: "Description",
    amount: "Amount", debit: "Money Out", credit: "Money In" });
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fmt = (file?.name.split(".").pop() ?? "csv").toLowerCase();
  const isCsv = fmt === "csv";

  const doPreview = (e: React.FormEvent) => {
    e.preventDefault();
    if (!file || !accountId) return;
    setBusy(true); setError(null);
    const mapping = isCsv ? JSON.stringify({
      date: cols.date, payee: cols.payee,
      amount: cols.amount || undefined,
      debit: cols.debit || undefined,
      credit: cols.credit || undefined,
    }) : "{}";
    api.upload("/api/v1/imports/preview", file, {
      account_id: accountId, fmt, mapping,
    }).then((r) => setPreview(r as Preview))
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const doCommit = () => {
    if (!preview) return;
    setBusy(true);
    api.post("/api/v1/imports/{import_id}/commit",
      { path: { import_id: preview.import_row.id } })
      .then((r) => {
        const imp = r as Preview["import_row"];
        toast.success(`Imported ${imp.rows_imported} transaction${imp.rows_imported === 1 ? "" : "s"}${imp.rows_skipped ? `, ${imp.rows_skipped} duplicates skipped` : ""}`);
        onDone();
      })
      .catch((err: Error) => { setError(err.message); setBusy(false); });
  };

  const dupCount = preview?.rows.filter((r) => r.duplicate).length ?? 0;
  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}
      title="Import statement"
      description="CSV, OFX or QIF from your bank. Duplicates are skipped automatically."
      footer={preview ? (
        <>
          <Button variant="ghost" onClick={() => setPreview(null)}>Back</Button>
          <Button variant="primary" onClick={doCommit}
            disabled={busy || preview.rows.length - dupCount === 0}>
            {busy ? "Importing…" : `Import ${preview.rows.length - dupCount} transaction${preview.rows.length - dupCount === 1 ? "" : "s"}`}
          </Button>
        </>
      ) : (
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" type="submit" form="ff-import"
            disabled={busy || !file || !accountId}>
            {busy ? "Parsing…" : "Preview"}
          </Button>
        </>
      )}>
      {preview ? (
        <div className="max-h-72 overflow-auto">
          <table className="w-full text-[13px]">
            <tbody>
              {preview.rows.slice(0, 50).map((r: Row, i) => (
                <tr key={i} className={r.duplicate ? "opacity-50" : ""}>
                  <td className="py-1 pr-2 text-fg-subtle">{r.date}</td>
                  <td className="truncate py-1 pr-2">{r.payee}</td>
                  <td className="py-1 pr-2 text-right">
                    <Money pence={r.amount_pence} signed tone="auto" /></td>
                  <td className="w-16 text-right">
                    {r.duplicate && <Badge tone="neutral">dup</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {preview.rows.length > 50 && (
            <p className="mt-2 text-[12px] text-fg-subtle">
              …and {preview.rows.length - 50} more
            </p>)}
          {dupCount > 0 && (
            <p className="mt-2 text-[12.5px] text-fg-subtle">
              {dupCount} already imported — will be skipped.
            </p>)}
        </div>
      ) : (
        <form id="ff-import" onSubmit={doPreview} className="grid gap-4">
          <Field label="Account">{(id) => (
            <Select id={id} aria-label="Import into account"
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}>
              <option value="">Choose account…</option>
              {accounts.filter((a) => !a.archived).map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>))}
            </Select>
          )}</Field>
          <Field label="Statement file">{(id) => (
            <Input id={id} aria-label="Statement file" type="file"
              accept=".csv,.ofx,.qif"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          )}</Field>
          {isCsv && (
            <div className="grid gap-3 rounded-xl border border-border p-3 sm:grid-cols-2">
              <p className="col-span-full text-[12.5px] text-fg-subtle">
                Column names — use either Amount <em>or</em> the
                Money Out / Money In pair.
              </p>
              {(["date", "payee", "amount", "debit", "credit"] as const)
                .map((k) => (
                  <Field key={k}
                    label={k === "debit" ? "Money out" : k === "credit" ? "Money in" : k[0]!.toUpperCase() + k.slice(1)}>
                    {(id) => (
                      <Input id={id} aria-label={`${k} column`}
                        value={cols[k]}
                        onChange={(e) =>
                          setCols((c) => ({ ...c, [k]: e.target.value }))} />
                    )}
                  </Field>
                ))}
            </div>
          )}
        </form>
      )}
      {error && <p role="alert" className="text-[13px] text-negative">{error}</p>}
    </Dialog>
  );
}
