/** Settings: household members and roles, category management,
 * appearance (system/light/dark), and data — version, backups and
 * export. */
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { undoToast } from "../lib/toast";
import {
  CalendarClock, Download, HardDrive, Monitor, Moon, Palette, Pencil,
  Plus, Sun, Tags, Upload, UserRound, Users,
} from "lucide-react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { useReady } from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Panel } from "../components/Panel";
import { Badge, type BadgeTone } from "../components/Badge";
import { Button } from "../components/Button";
import { Dialog } from "../components/Dialog";
import { Field, Input, Select } from "../components/Field";
import { IconTile, PageHeader, Segmented } from "../components/Display";
import { EmptyState } from "../components/Feedback";
import { setThemePref, useTheme, type ThemePref } from "../theme/useTheme";
import { formatBytes } from "../lib/format";

type Profile = components["schemas"]["ProfileOut"];
type Role = components["schemas"]["Role"];
type Category = components["schemas"]["CategoryOut"];
type Backup = components["schemas"]["BackupOut"];
type Employment = components["schemas"]["EmploymentOut"];
type PayrollRun = components["schemas"]["PayrollRunOut"];

const ROLE_TONE: Record<Role, BadgeTone> = {
  admin: "accent", member: "info", viewer: "neutral",
};

export function Settings() {
  const ready = useReady();
  const [rev, setRev] = useState(0);
  const [addingMember, setAddingMember] = useState(false);
  const [linking, setLinking] = useState<Profile | null>(null);
  const [addingCategory, setAddingCategory] = useState(false);
  const [editingCategory, setEditingCategory] = useState<Category | null>(null);
  const profiles = useApi(() => api.get("/api/v1/profiles"), ready, [rev]);
  const categories = useApi(() => api.get("/api/v1/categories"), ready, [rev]);
  const version = useApi(() => api.get("/api/v1/version"), ready);
  const backups = useApi(() => api.get("/api/v1/admin/backups"), ready, [rev]);
  const { pref } = useTheme();
  const refresh = () => setRev((r) => r + 1);

  const toggleMember = (p: Profile) =>
    api.post(
      p.is_active
        ? "/api/v1/profiles/{profile_id}/deactivate"
        : "/api/v1/profiles/{profile_id}/activate",
      { path: { profile_id: p.id } },
    ).then(() => {
      undoToast(`${p.display_name} ${p.is_active ? "deactivated" : "activated"}`,
        () => api.post(
          p.is_active
            ? "/api/v1/profiles/{profile_id}/activate"
            : "/api/v1/profiles/{profile_id}/deactivate",
          { path: { profile_id: p.id } },
        ).then(refresh));
      refresh();
    }).catch((e: Error) => toast.error(e.message));

  const archiveCategory = (c: Category) =>
    api.patch("/api/v1/categories/{category_id}", {
      path: { category_id: c.id },
      body: { archived: !c.archived },
    }).then(() => {
      const done = c.archived ? `${c.name} restored` : `${c.name} archived`;
      undoToast(done, () =>
        api.patch("/api/v1/categories/{category_id}", {
          path: { category_id: c.id },
          body: { archived: c.archived },
        }).then(refresh));
      refresh();
    }).catch((e: Error) => toast.error(e.message));

  const backupNow = () =>
    api.post("/api/v1/admin/backups")
      .then((b) => { toast.success(`Backup saved (${formatBytes(b.bytes)})`); refresh(); })
      .catch((e: Error) => toast.error(e.message));

  const restoreFile = useRef<HTMLInputElement>(null);
  const [pendingRestore, setPendingRestore] = useState<File | null>(null);
  const downloadSnapshot = (name: string) =>
    api.download(`/api/v1/admin/backups/${name}/download`)
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url; a.download = name; a.click();
        URL.revokeObjectURL(url);
      })
      .catch((e: Error) => toast.error(e.message));
  const uploadSnapshot = () => {
    const f = pendingRestore;
    if (!f) return;
    setPendingRestore(null);
    api.upload("/api/v1/admin/restore/upload", f)
      .then(() => toast.success("Restore queued — restart the app to apply it"))
      .catch((e: Error) => toast.error(e.message));
  };

  const cats = categories.data?.items ?? [];
  const parents = cats.filter((c) => c.parent_id === null);

  return (
    <div>
      <PageHeader title="Settings" description="Household, categories, appearance and your data." />

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <Panel
          flush
          title="Household members"
          description="Who shares this ledger and what they can do."
          actions={<Button size="sm" variant="secondary" onClick={() => setAddingMember(true)}><Plus /> Add</Button>}
        >
          <DataView
            state={{ ...profiles, data: profiles.data?.items ?? null }}
            empty="No members"
            emptyIcon={Users}
          >
            {(items) => (
              <ul className="divide-y divide-border">
                {items.map((p) => (
                  <li key={p.id} className="flex items-center gap-3 px-4 py-3">
                    <IconTile icon={UserRound} label={p.display_name} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13.5px] font-medium">{p.display_name}</p>
                      <p className="text-[12px] text-fg-subtle">
                        {p.is_active ? "Active" : "Deactivated"}
                        {p.ha_user_id ? " · HA linked" : ""}
                      </p>
                    </div>
                    <Badge tone={ROLE_TONE[p.role]}>{p.role}</Badge>
                    {!p.ha_user_id && (
                      <Button size="sm" variant="ghost" onClick={() => setLinking(p)}
                        aria-label={`Link ${p.display_name} to a Home Assistant account`}>
                        Link
                      </Button>
                    )}
                    <Button size="sm" variant="ghost" onClick={() => toggleMember(p)}
                      aria-label={`${p.is_active ? "Deactivate" : "Activate"} ${p.display_name}`}>
                      {p.is_active ? "Deactivate" : "Activate"}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </DataView>
        </Panel>

        <Panel
          flush
          title="Categories"
          description="The buckets your spending is sorted into."
          actions={<Button size="sm" variant="secondary" onClick={() => setAddingCategory(true)}><Plus /> Add</Button>}
        >
          <DataView
            state={{ ...categories, data: cats }}
            empty="No categories"
            emptyIcon={Tags}
          >
            {() => (
              <ul className="max-h-105 divide-y divide-border overflow-y-auto scrollbar-thin">
                {parents.map((c) => (
                  <li key={c.id}>
                    <div className="flex items-center gap-3 px-4 py-2.5">
                      <IconTile label={c.name} size="sm" />
                      <span className={c.archived ? "flex-1 text-[13.5px] font-medium text-fg-subtle line-through" : "flex-1 text-[13.5px] font-medium"}>
                        {c.name}
                        {c.is_system && <Badge tone="neutral" className="ml-2">system</Badge>}
                        {cats.some((ch) => ch.parent_id === c.id) && (
                          <Badge tone="info" className="ml-2">section</Badge>
                        )}
                      </span>
                      <Button size="sm" variant="ghost" onClick={() => setEditingCategory(c)}
                        aria-label={`Edit ${c.name}`}><Pencil /></Button>
                      <Button size="sm" variant="ghost" onClick={() => archiveCategory(c)}
                        aria-label={`${c.archived ? "Restore" : "Archive"} ${c.name}`}>
                        {c.archived ? "Restore" : "Archive"}
                      </Button>
                    </div>
                    {cats.filter((ch) => ch.parent_id === c.id).map((ch) => (
                      <div key={ch.id} className="flex items-center gap-3 py-2 pr-4 pl-13">
                        <span className={ch.archived ? "flex-1 text-[13px] text-fg-subtle line-through" : "flex-1 text-[13px] text-fg-muted"}>{ch.name}</span>
                        <Button size="sm" variant="ghost" onClick={() => setEditingCategory(ch)}
                          aria-label={`Edit ${ch.name}`}><Pencil /></Button>
                        <Button size="sm" variant="ghost" onClick={() => archiveCategory(ch)}
                          aria-label={`${ch.archived ? "Restore" : "Archive"} ${ch.name}`}>
                          {ch.archived ? "Restore" : "Archive"}
                        </Button>
                      </div>
                    ))}
                  </li>
                ))}
              </ul>
            )}
          </DataView>
        </Panel>

        <YearPrepPanel />

        <Panel title="Appearance" description="Follows your system by default — override here.">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <IconTile icon={Palette} label="theme" />
              <div>
                <p className="text-[13.5px] font-medium">Theme</p>
                <p className="text-[12px] text-fg-subtle">Applies everywhere, remembered on this device.</p>
              </div>
            </div>
            <Segmented<ThemePref>
              label="Theme"
              value={pref}
              onChange={setThemePref}
              options={[
                { value: "system", label: <span className="inline-flex items-center gap-1"><Monitor className="size-3.5" /> System</span> },
                { value: "light", label: <span className="inline-flex items-center gap-1"><Sun className="size-3.5" /> Light</span> },
                { value: "dark", label: <span className="inline-flex items-center gap-1"><Moon className="size-3.5" /> Dark</span> },
              ]}
            />
          </div>
        </Panel>

        <Panel
          title="Data & backups"
          description={`Family Finance ${version.data ? String(version.data["version"] ?? "") : ""}`}
        >
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" onClick={backupNow}><HardDrive /> Back up now</Button>
            <a
              href="./api/v1/transactions/export.csv"
              className="inline-flex h-9 items-center gap-2 rounded-lg border border-border bg-surface px-3.5 text-sm font-medium hover:bg-surface-2 [&_svg]:size-4"
            >
              <Download /> Export transactions (CSV)
            </a>
            <Button variant="secondary" onClick={() => restoreFile.current?.click()}>
              <Upload /> Restore from file…
            </Button>
            <input ref={restoreFile} type="file" accept=".db" className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) setPendingRestore(f);
                e.target.value = "";
              }} />
          </div>
          {(backups.data ?? []).length > 0 ? (
            <ul className="mt-3 divide-y divide-border rounded-lg border border-border">
              {(backups.data ?? []).slice(0, 5).map((b: Backup) => (
                <li key={b.name} className="flex items-center justify-between gap-3 px-3 py-2 text-[12.5px]">
                  <span className="truncate font-medium">{b.name}</span>
                  <span className="flex shrink-0 items-center gap-1">
                    <span className="tnum text-fg-subtle">{formatBytes(b.bytes)}{b.ok ? "" : " · incomplete"}</span>
                    <Button size="sm" variant="ghost" onClick={() => downloadSnapshot(b.name)}
                      aria-label={`Download ${b.name}`} title="Download snapshot"><Download /></Button>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState compact icon={HardDrive} title="No backups yet"
              description="Nightly backups run automatically — or take one now." />
          )}
        </Panel>
      </div>

      <Dialog open={pendingRestore !== null}
        onOpenChange={(o) => { if (!o) setPendingRestore(null); }}
        title="Restore from snapshot?">
        <p className="text-[13px] text-fg-muted">
          <strong>{pendingRestore?.name}</strong> will replace <strong>all</strong> current
          data when the app next starts. This cannot be undone — take a backup first
          if in doubt.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setPendingRestore(null)}>Cancel</Button>
          <Button variant="primary" onClick={uploadSnapshot}>Queue restore</Button>
        </div>
      </Dialog>
      <AddMemberDialog open={addingMember} onOpenChange={setAddingMember} onSaved={refresh} />
      {linking && (
        <LinkIdentityDialog profile={linking} onClose={() => setLinking(null)} onSaved={refresh} />
      )}
      <AddCategoryDialog open={addingCategory} onOpenChange={setAddingCategory} parents={parents.filter((c) => !c.archived)} onSaved={refresh} />
      <EditCategoryDialog
        category={editingCategory}
        categories={cats}
        onClose={() => setEditingCategory(null)}
        onSaved={() => { setEditingCategory(null); refresh(); }}
      />
    </div>
  );
}

/** Year preparation (issue #304): roll payroll runs + budget
 * structure forward one year — the button that used to live on
 * the spreadsheet's "new year" sheet. */
function YearPrepPanel() {
  const ready = useReady();
  const employments = useApi(() => api.get("/api/v1/employments"), ready);
  const items = employments.data?.items ?? [];
  const [refreshToken, setRefreshToken] = useState(0);
  const employmentIds = items.map((e: Employment) => e.id).join(",");
  const runs = useApi(
    () => Promise.all(items.map((e: Employment) => api.get(
      "/api/v1/employments/{emp_id}/payroll-runs", { path: { emp_id: e.id } }))),
    ready && items.length > 0, [employmentIds, refreshToken]);
  const budgets = useApi(() => api.get("/api/v1/budgets"), ready, [refreshToken]);
  const runHorizon = (runs.data ?? []).flat()
    .reduce<number | null>((max, run: PayrollRun) => {
      const year = Number(run.pay_date.slice(0, 4));
      return max === null || year > max ? year : max;
    }, null);
  const budgetHorizon = (budgets.data?.items ?? [])
    .reduce<number | null>((max, budget) => {
      const year = Number(String(budget.end_date).slice(0, 4));
      return max === null || year > max ? year : max;
    }, null);
  const horizonYear = Math.max(
    runHorizon ?? new Date().getFullYear(),
    budgetHorizon ?? new Date().getFullYear());
  const defaultYear = horizonYear + 1;
  const [selectedYear, setSelectedYear] = useState<number | null>(null);
  useEffect(() => {
    if (selectedYear === null) setSelectedYear(defaultYear);
  }, [defaultYear, horizonYear, selectedYear]);
  const year = selectedYear ?? defaultYear;
  const [prepping, setPrepping] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const prepare = () => {
    setConfirming(false);
    setPrepping(true);
    api.post("/api/v1/employments/prepare-year", { body: { year } })
      .then((r) => {
        setResult(
          `${r.year}: ${r.runs_created} payroll runs created`
          + `${r.runs_skipped ? `, ${r.runs_skipped} already existed` : ""}`
          + ` · ${r.budgets_created} budgets rolled forward`);
        for (const w of r.warnings) toast.warning(w);
        setRefreshToken((value) => value + 1);
      })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setPrepping(false));
  };
  return (
    <Panel title="Year preparation"
      description="Create next year's forecast payroll runs for every active employer and roll the budget structure forward — the spreadsheet's 'new year sheet' as a button.">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Year">{(id) => (
          <Select id={id} value={String(year)}
            onChange={(e) => setSelectedYear(Number(e.target.value))}>
            {Array.from({ length: 7 }, (_, offset) => defaultYear - 3 + offset).map((option) => {
              return <option key={option} value={option}>{option}</option>;
            })}
          </Select>
        )}</Field>
        <Button variant="secondary" onClick={() => setConfirming(true)} disabled={prepping}>
          <CalendarClock /> {prepping ? "Preparing…" : `Prepare ${year}`}
        </Button>
        {result && <span className="text-[12.5px] text-fg-muted">{result}</span>}
      </div>
      <Dialog open={confirming} onOpenChange={setConfirming}
        title={`Prepare ${year}?`}
        description="This creates forecast payroll runs and copies budget structure for the selected year. Existing records are not overwritten."
        footer={<>
          <Button variant="ghost" onClick={() => setConfirming(false)}>Cancel</Button>
          <Button variant="primary" onClick={prepare}>Confirm preparation</Button>
        </>}>
        <p className="text-[13px]">
          Prepare {year} now? This action is idempotent, but it may add forecast
          runs and budget rows.
        </p>
      </Dialog>
    </Panel>
  );
}

function AddMemberDialog({ open, onOpenChange, onSaved }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [busy, setBusy] = useState(false);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    api.post("/api/v1/profiles", { body: { display_name: name.trim(), role } })
      .then(() => {
        toast.success(`${name.trim()} added`);
        setName("");
        onSaved();
        onOpenChange(false);
      })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Add member"
      description="Members get their own dashboard layout; admins can manage the household."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-add-member" disabled={!name.trim() || busy}>{busy ? "Adding…" : "Add member"}</Button>
      </>}>
      <form id="ff-add-member" onSubmit={submit} className="grid gap-4">
        <Field label="Name">{(id) => <Input id={id} autoFocus placeholder="e.g. Alex" value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <Field label="Role">{(id) => (
          <Select id={id} value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="member">Member — full use</option>
            <option value="admin">Admin — manage household</option>
            <option value="viewer">Viewer — read only</option>
          </Select>
        )}</Field>
      </form>
    </Dialog>
  );
}

function LinkIdentityDialog({ profile, onClose, onSaved }: {
  profile: Profile;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [uid, setUid] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!uid.trim()) return;
    setBusy(true);
    api.patch("/api/v1/profiles/{profile_id}", {
      path: { profile_id: profile.id },
      body: { ha_user_id: uid.trim() },
    })
      .then(() => {
        toast.success(`${profile.display_name} linked to Home Assistant`);
        onSaved();
        onClose();
      })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}
      title={`Link ${profile.display_name}`}
      description="Paste their Home Assistant user ID (Settings → People → Users). If a duplicate member holds it, that copy is deactivated."
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-link-identity" disabled={!uid.trim() || busy}>{busy ? "Linking…" : "Link account"}</Button>
      </>}>
      <form id="ff-link-identity" onSubmit={submit} className="grid gap-4">
        <Field label="Home Assistant user ID">{(id) => (
          <Input id={id} autoFocus placeholder="e.g. 6855b4b31c3d4b83b777fa075e1b1a76"
            value={uid} onChange={(e) => setUid(e.target.value)} />
        )}</Field>
      </form>
    </Dialog>
  );
}

function AddCategoryDialog({ open, onOpenChange, parents, onSaved }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  parents: Category[];
  onSaved: () => void;
}) {
  const [name, setName] = useState("");
  const [parentId, setParentId] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    api.post("/api/v1/categories", {
      body: { name: name.trim(), parent_id: parentId ? Number(parentId) : null, is_system: false },
    })
      .then(() => {
        toast.success(`${name.trim()} added`);
        setName("");
        onSaved();
        onOpenChange(false);
      })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Add category"
      description="Top-level or nested under an existing category."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-add-cat" disabled={!name.trim() || busy}>{busy ? "Adding…" : "Add category"}</Button>
      </>}>
      <form id="ff-add-cat" onSubmit={submit} className="grid gap-4">
        <Field label="Name">{(id) => <Input id={id} autoFocus placeholder="e.g. Eating out" value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <Field label="Parent" hint="Optional — nest under a top-level category.">{(id) => (
          <Select id={id} value={parentId} onChange={(e) => setParentId(e.target.value)}>
            <option value="">Top level</option>
            {parents.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        )}</Field>
      </form>
    </Dialog>
  );
}

/** Rename / reparent an existing category (issue #273). Parent options
 * exclude the category itself and — since hierarchy is one level deep —
 * any category that already has a parent; a category with children is
 * also excluded from being nested, keeping depth at one. */
function EditCategoryDialog({ category, categories, onClose, onSaved }: {
  category: Category | null;
  categories: Category[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState("");
  const [parentId, setParentId] = useState("");
  const [busy, setBusy] = useState(false);
  const [openFor, setOpenFor] = useState<number | null>(null);
  // Sync local state when a different category is opened for editing.
  if (category && category.id !== openFor) {
    setOpenFor(category.id);
    setName(category.name);
    setParentId(category.parent_id != null ? String(category.parent_id) : "");
  }
  if (!category) return null;
  const hasChildren = categories.some((c) => c.parent_id === category.id);
  const parentOptions = categories.filter((c) =>
    c.id !== category.id && c.parent_id === null && !c.archived &&
    !c.is_system);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    api.patch("/api/v1/categories/{category_id}", {
      path: { category_id: category.id },
      body: {
        name: name.trim(),
        parent_id: hasChildren ? null : (parentId ? Number(parentId) : null),
      },
    })
      .then(() => { toast.success(`${name.trim()} saved`); onSaved(); })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setBusy(false));
  };
  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }} title={`Edit ${category.name}`}
      description="Rename it or move it under a top-level category to form a budget section."
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-edit-cat" disabled={!name.trim() || busy}>{busy ? "Saving…" : "Save"}</Button>
      </>}>
      <form id="ff-edit-cat" onSubmit={submit} className="grid gap-4">
        <Field label="Name">{(id) => <Input id={id} autoFocus value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        {hasChildren ? (
          <p className="text-[12.5px] text-fg-subtle">
            This category is a section — remove its {categories.filter((c) => c.parent_id === category.id).length} child
            categor{categories.filter((c) => c.parent_id === category.id).length === 1 ? "y" : "ies"} first to nest it.
          </p>
        ) : category.is_system ? (
          <p className="text-[12.5px] text-fg-subtle">System categories can be renamed but not re-parented.</p>
        ) : (
          <Field label="Parent" hint="Nest under a top-level category, or leave at top level.">{(id) => (
            <Select id={id} value={parentId} onChange={(e) => setParentId(e.target.value)}>
              <option value="">Top level</option>
              {parentOptions.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </Select>
          )}</Field>
        )}
      </form>
    </Dialog>
  );
}
