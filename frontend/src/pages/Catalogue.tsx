/**
 * Component catalogue (issue #122) — every core component in every
 * state, browsable at /catalogue. The lightweight equivalent of
 * Storybook: no extra dependency, always in sync because it imports
 * the real components.
 */
import { useState } from "react";
import { Inbox, Plus, Wallet } from "lucide-react";
import { Badge, type BadgeTone } from "../components/Badge";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { Money, HeroMoney } from "../components/Money";
import { Panel } from "../components/Panel";
import { DataView } from "../components/DataView";
import { Dialog } from "../components/Dialog";
import {
  Checkbox, Field, Input, SearchInput, Select, Textarea,
} from "../components/Field";
import {
  IconTile, PageHeader, Progress, Ring, Segmented, Stat,
} from "../components/Display";
import { EmptyState, ErrorState, Skeleton } from "../components/Feedback";
import { formatDate, formatDateTime, formatPence } from "../lib/format";

const TONES: BadgeTone[] = ["neutral", "info", "success", "warning", "error", "accent"];

function Swatch({ name, className }: { name: string; className: string }) {
  return (
    <div className="flex items-center gap-2.5">
      <span className={`size-8 rounded-lg border border-border ${className}`} />
      <code className="text-[12px] text-fg-muted">{name}</code>
    </div>
  );
}

export function Catalogue() {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [seg, setSeg] = useState<"a" | "b" | "c">("a");
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Catalogue"
        description="Every shared component in every state — the living style guide."
      />

      <Panel title="Colour tokens">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          <Swatch name="bg" className="bg-bg" />
          <Swatch name="surface" className="bg-surface" />
          <Swatch name="surface-2" className="bg-surface-2" />
          <Swatch name="surface-3" className="bg-surface-3" />
          <Swatch name="accent" className="bg-accent" />
          <Swatch name="positive" className="bg-positive" />
          <Swatch name="negative" className="bg-negative" />
          <Swatch name="warning" className="bg-warning" />
        </div>
      </Panel>

      <Panel title="Buttons">
        <div className="flex flex-wrap items-center gap-2">
          {(["primary", "secondary", "ghost", "accent", "danger", "link"] as const).map((v) => (
            <Button key={v} variant={v}>{v}</Button>
          ))}
          <Button variant="primary" disabled>disabled</Button>
          <Button variant="primary"><Plus /> with icon</Button>
          <Button variant="secondary" size="sm">sm</Button>
          <Button variant="secondary" size="lg">lg</Button>
        </div>
      </Panel>

      <Panel title="Badges">
        <div className="flex flex-wrap gap-2">
          {TONES.map((t) => <Badge key={t} tone={t}>{t}</Badge>)}
          {TONES.map((t) => <Badge key={`${t}-d`} tone={t} dot={false}>{t}</Badge>)}
        </div>
      </Panel>

      <Panel title="Money" actions={<Badge tone="info">tabular-nums</Badge>}>
        <div className="flex flex-col gap-2">
          <p>
            <Money pence={123456} /> · <Money pence={0} /> ·{" "}
            <Money pence={-4200} /> · <Money pence={99999999} />
          </p>
          <p>
            signed: <Money pence={1250} signed /> · <Money pence={-1250} signed /> ·
            tone auto: <Money pence={1250} signed tone="auto" /> · <Money pence={-1250} signed tone="auto" /> ·
            variance: <Money pence={-1250} signed tone="variance" />
          </p>
          <p className="text-[28px]"><HeroMoney pence={128460} /></p>
        </div>
      </Panel>

      <Panel title="Stats, progress and rings">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Safe to spend" value={<Money pence={75000} />} hint="until payday" delta={0.042} />
          <Stat label="Spend this month" value={<Money pence={142300} />} delta={-0.08} deltaGoodWhen="down" icon={Wallet} />
          <div className="flex flex-col gap-1.5">
            <p className="text-[13px] text-fg-muted">Progress (auto tones)</p>
            <Progress value={40} max={100} label="40%" />
            <Progress value={92} max={100} label="92%" />
            <Progress value={110} max={100} label="110%" />
          </div>
          <div className="flex items-center gap-4">
            <Ring ratio={0.68} size={84} stroke={9}>
              <span className="tnum text-[15px] font-semibold">68%</span>
            </Ring>
            <Ring ratio={1.05} size={84} stroke={9}>
              <span className="tnum text-[15px] font-semibold text-negative">105%</span>
            </Ring>
          </div>
        </div>
      </Panel>

      <Panel title="Controls">
        <div className="grid max-w-2xl gap-4 sm:grid-cols-2">
          <Field label="Text input" hint="With a hint.">
            {(id) => <Input id={id} placeholder="Type here…" />}
          </Field>
          <Field label="Select">{(id) => (
            <Select id={id}><option>Option A</option><option>Option B</option></Select>
          )}</Field>
          <Field label="Search">{(id) => <SearchInput id={id} placeholder="Search…" />}</Field>
          <Field label="Error" error="Something is wrong.">
            {(id) => <Input id={id} defaultValue="bad value" />}
          </Field>
          <Field label="Textarea" className="sm:col-span-2">
            {(id) => <Textarea id={id} placeholder="Notes…" />}
          </Field>
          <label className="flex items-center gap-2 text-[13.5px]">
            <Checkbox defaultChecked /> Checkbox
          </label>
          <Segmented
            label="Segmented"
            value={seg}
            onChange={setSeg}
            options={[{ value: "a", label: "Day" }, { value: "b", label: "Week" }, { value: "c", label: "Month" }]}
          />
        </div>
      </Panel>

      <Panel title="Icon tiles">
        <div className="flex flex-wrap gap-3">
          {["Council tax", "Tesco", "Spotify", "Salary", "Savings", "Vet", "Sky TV", "Petrol"].map((l) => (
            <div key={l} className="flex flex-col items-center gap-1.5">
              <IconTile label={l} size="lg" />
              <span className="text-[11px] text-fg-subtle">{l}</span>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="Feedback states">
        <div className="grid gap-4 lg:grid-cols-3">
          <EmptyState
            icon={Inbox}
            title="Nothing here yet"
            description="Empty states explain what's missing and how to fill it."
            action={<Button size="sm" variant="primary">Do the thing</Button>}
          />
          <ErrorState message="Sync failed: couldn't reach the bank feed." onRetry={() => {}} />
          <div className="space-y-2.5">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-4 w-3/5" />
          </div>
        </div>
      </Panel>

      <Panel title="Dialog">
        <Button variant="secondary" onClick={() => setDialogOpen(true)}>Open dialog</Button>
        <Dialog
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          title="Example dialog"
          description="Radix-backed: focus trap, Escape, scroll lock."
          footer={<Button variant="primary" onClick={() => setDialogOpen(false)}>Done</Button>}
        >
          <p className="text-[13.5px] text-fg-muted">Body content goes here.</p>
        </Dialog>
      </Panel>

      <Panel title="Formatters">
        <p>formatPence(123456) → {formatPence(123456)}</p>
        <p>formatDate → {formatDate("2026-03-15T10:30:00Z")}</p>
        <p>formatDateTime → {formatDateTime("2026-03-15T10:30:00Z")}</p>
        <p>invalid → {formatDate("not-a-date")}</p>
      </Panel>

      <Card title="DataView states">
        <DataView state={{ loading: true, retrying: false, error: null, data: null, refetch: () => {} }} empty="unused">
          {() => null}
        </DataView>
        <DataView state={{ loading: false, retrying: false, error: "Sync failed", data: null, refetch: () => {} }} empty="unused">
          {() => null}
        </DataView>
        <DataView state={{ loading: false, retrying: false, error: null, data: [], refetch: () => {} }} empty="Nothing here yet">
          {() => null}
        </DataView>
        <DataView state={{ loading: false, retrying: false, error: null, data: ["row"], refetch: () => {} }} empty="unused">
          {(d) => <p>data: {d.join(",")}</p>}
        </DataView>
      </Card>
    </div>
  );
}
