/** Rules: the categorisation automation surface (issue #275). Each
 * rule maps a payee pattern to a category — contains/exact/regex match,
 * ordered by priority; auto_apply rules set the category without
 * review, others leave a suggestion. Learned rules (from corrections)
 * appear alongside manual ones. "Run now" applies them to
 * uncategorised transactions. */
import { useState } from "react";
import { toast } from "sonner";
import { undoToast } from "../lib/toast";
import { Play, Plus, Trash2, Wand2 } from "lucide-react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { useCategories, useReady } from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Dialog } from "../components/Dialog";
import { Field, Input, Select } from "../components/Field";
import { IconTile, PageHeader, Stat } from "../components/Display";
import { Skeleton } from "../components/Feedback";
import { cn } from "../lib/cn";

type Rule = components["schemas"]["CategorisationRuleOut"];
type Category = components["schemas"]["CategoryOut"];

const MATCH_LABEL: Record<string, string> = {
  contains: "contains", exact: "exact", regex: "regex",
};

export function Rules() {
  const ready = useReady();
  const [rev, setRev] = useState(0);
  const rules = useApi(() => api.get("/api/v1/categorisation/rules"), ready, [rev]);
  const categories = useCategories();
  const [adding, setAdding] = useState(false);
  const refresh = () => setRev((r) => r + 1);

  const items = rules.data ?? [];
  const autoCount = items.filter((r) => r.auto_apply && r.enabled).length;
  const learned = items.filter((r) => r.source === "learned").length;

  const catName = (id: number) => categories.byId.get(id)?.name ?? "—";

  const toggleEnabled = (r: Rule) =>
    api.patch("/api/v1/categorisation/rules/{rule_id}", {
      path: { rule_id: r.id }, body: { enabled: !r.enabled },
    }).then(() => refresh())
      .catch((e: Error) => toast.error(e.message));

  const toggleAuto = (r: Rule) =>
    api.patch("/api/v1/categorisation/rules/{rule_id}", {
      path: { rule_id: r.id }, body: { auto_apply: !r.auto_apply },
    }).then(() => refresh())
      .catch((e: Error) => toast.error(e.message));

  const remove = (r: Rule) =>
    api.delete("/api/v1/categorisation/rules/{rule_id}", {
      path: { rule_id: r.id },
    }).then(() => {
      undoToast(`Rule for “${r.pattern}” deleted`, () =>
        api.post("/api/v1/categorisation/rules", {
          body: {
            pattern: r.pattern, match_type: r.match_type,
            category_id: r.category_id, priority: r.priority,
            auto_apply: r.auto_apply,
          },
        }).then(refresh));
      refresh();
    }).catch((e: Error) => toast.error(e.message));

  const runNow = () =>
    api.post("/api/v1/categorisation/apply")
      .then((r) => {
        toast.success(
          r.auto_applied + r.suggested + r.unmatched === 0
            ? "Nothing to categorise"
            : `${r.auto_applied} applied · ${r.suggested} suggested · ${r.unmatched} unmatched`);
        refresh();
      }).catch((e: Error) => toast.error(e.message));

  return (
    <div>
      <PageHeader
        title="Rules"
        description="Teach the app once — every matching transaction gets categorised from then on."
        actions={
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" onClick={runNow}>
              <Play /> Run now
            </Button>
            <Button variant="primary" size="sm" onClick={() => setAdding(true)}>
              <Plus /> New rule
            </Button>
          </div>
        }
      />

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-3">
        <Stat label="Active rules" value={String(items.filter((r) => r.enabled).length)}
          className="rounded-xl border border-border bg-surface p-4 shadow-card" />
        <Stat label="Auto-applying" value={String(autoCount)}
          hint="set category without review"
          className="rounded-xl border border-border bg-surface p-4 shadow-card" />
        <Stat label="Learned" value={String(learned)}
          hint="from your corrections"
          className="hidden rounded-xl border border-border bg-surface p-4 shadow-card lg:block" />
      </div>

      <DataView
        state={{ ...rules, data: items }}
        empty="No rules yet"
        emptyIcon={Wand2}
        emptyDescription="Create a rule — or correct a transaction's category and the app learns one for you."
        emptyAction={<Button variant="primary" onClick={() => setAdding(true)}><Plus /> New rule</Button>}
        loading={<Skeleton className="h-64 w-full rounded-xl" />}
      >
        {(list) => (
          <ul className="divide-y divide-border rounded-xl border border-border bg-surface">
            {list.map((r) => (
              <li key={r.id}
                className={cn("flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-3",
                  !r.enabled && "opacity-55")}>
                <IconTile label={catName(r.category_id)} size="sm" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13.5px] font-medium">
                    “{r.pattern}” <span className="font-normal text-fg-subtle">→</span> {catName(r.category_id)}
                  </p>
                  <p className="text-[12px] text-fg-subtle">
                    {MATCH_LABEL[r.match_type] ?? r.match_type} · priority {r.priority}
                    {r.times_confirmed > 0 && ` · confirmed ${r.times_confirmed}×`}
                  </p>
                </div>
                {r.source === "learned" && <Badge tone="neutral">learned</Badge>}
                <button
                  type="button"
                  role="switch"
                  aria-checked={r.auto_apply}
                  aria-label={`Auto-apply “${r.pattern}”`}
                  onClick={() => toggleAuto(r)}
                  className={cn("rounded-full border px-2.5 py-0.5 text-[11.5px] font-medium transition-colors",
                    r.auto_apply
                      ? "border-accent/50 bg-accent-soft text-accent"
                      : "border-border text-fg-subtle hover:text-fg")}
                >
                  auto
                </button>
                <button
                  type="button"
                  role="switch"
                  aria-checked={r.enabled}
                  aria-label={`Enable “${r.pattern}”`}
                  onClick={() => toggleEnabled(r)}
                  className={cn("relative h-5 w-9 rounded-full transition-colors",
                    r.enabled ? "bg-accent" : "bg-surface-2 border border-border")}
                >
                  <span className={cn("absolute top-0.5 size-4 rounded-full bg-white shadow transition-all",
                    r.enabled ? "left-[18px]" : "left-0.5")} />
                </button>
                <Button variant="ghost" size="icon-sm"
                  aria-label={`Delete rule “${r.pattern}”`}
                  onClick={() => remove(r)}>
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </DataView>

      {adding && (
        <RuleDialog
          categories={[...categories.byId.values()].sort((a, b) => a.name.localeCompare(b.name))}
          onClose={() => setAdding(false)}
          onSaved={() => { setAdding(false); refresh(); }}
        />
      )}
    </div>
  );
}

function RuleDialog({ categories, onClose, onSaved }: {
  categories: Category[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [pattern, setPattern] = useState("");
  const [matchType, setMatchType] = useState("contains");
  const [categoryId, setCategoryId] = useState("");
  const [priority, setPriority] = useState("100");
  const [autoApply, setAutoApply] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!pattern.trim() || !categoryId) return;
    setBusy(true);
    api.post("/api/v1/categorisation/rules", {
      body: {
        pattern: pattern.trim(), match_type: matchType,
        category_id: Number(categoryId),
        priority: Number(priority) || 100, auto_apply: autoApply,
      },
    }).then(() => { toast.success("Rule created"); onSaved(); })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}
      title="New rule"
      description="When a transaction's payee matches, it gets this category — automatically or as a suggestion."
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-rule"
          disabled={busy || !pattern.trim() || !categoryId}>
          {busy ? "Creating…" : "Create rule"}
        </Button>
      </>}>
      <form id="ff-rule" onSubmit={submit} className="grid gap-4">
        <Field label="Payee pattern" hint='e.g. "TESCO" matches "TESCO STORES 2341".'>{(id) => (
          <Input id={id} required value={pattern} autoFocus
            onChange={(e) => setPattern(e.target.value)}
            placeholder="Merchant text to match" />
        )}</Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Match">{(_id) => (
            <Select aria-label="Match type" value={matchType}
              onChange={(e) => setMatchType(e.target.value)}>
              <option value="contains">contains</option>
              <option value="exact">exact</option>
              <option value="regex">regex</option>
            </Select>
          )}</Field>
          <Field label="Priority" hint="Lower wins when several match.">{(_id) => (
            <Input aria-label="Rule priority" type="number" value={priority}
              onChange={(e) => setPriority(e.target.value)} />
          )}</Field>
        </div>
        <Field label="Category">{(_id) => (
          <Select aria-label="Rule category" required value={categoryId}
            onChange={(e) => setCategoryId(e.target.value)}>
            <option value="" disabled>Choose…</option>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </Select>
        )}</Field>
        <label className="flex cursor-pointer items-center gap-2.5 text-[13.5px]">
          <input
            type="checkbox"
            checked={autoApply}
            onChange={(e) => setAutoApply(e.target.checked)}
            className="size-4 rounded border-border accent-accent"
            aria-label="Auto-apply without review"
          />
          <span>
            Auto-apply
            <span className="ml-1.5 text-[12px] text-fg-subtle">
              — skip review for confident matches
            </span>
          </span>
        </label>
      </form>
    </Dialog>
  );
}
