/** Energy: this-month cost report (unit + standing + projection vs
 * budget), a daily-use chart built from meter readings, tariff history
 * and add-reading / add-tariff flows. */
import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Gauge, Plus, Zap } from "lucide-react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { useReady } from "../api/hooks";
import type { components } from "../api/schema";
import { Panel } from "../components/Panel";
import { Badge } from "../components/Badge";
import { Money } from "../components/Money";
import { Button } from "../components/Button";
import { Dialog } from "../components/Dialog";
import { Field, Input } from "../components/Field";
import { PageHeader, Stat } from "../components/Display";
import { EmptyState, Skeleton } from "../components/Feedback";
import {
  formatDayMonth, formatKwh, formatPence, formatSmartDate, formatUnitRate,
  toISODate,
} from "../lib/format";

type Tariff = components["schemas"]["EnergyTariffOut"];

export function Energy() {
  const ready = useReady();
  const [rev, setRev] = useState(0);
  const [addingReading, setAddingReading] = useState(false);
  const [addingTariff, setAddingTariff] = useState(false);
  const config = useApi(() => api.get("/api/v1/energy/config"), ready, [rev]);
  const tariffs = useApi(() => api.get("/api/v1/energy/tariffs"), ready, [rev]);
  const readings = useApi(() => api.get("/api/v1/energy/readings"), ready, [rev]);
  // Report/estimate 409 while nothing is set up — only fetch them once
  // there's a meter entity, a tariff or at least one reading.
  const configured = config.data?.meter_entity != null
    || (tariffs.data?.length ?? 0) > 0
    || (readings.data?.length ?? 0) > 0;
  const report = useApi(
    () => api.get("/api/v1/energy/report"),
    ready && configured, [rev, configured]);
  const estimate = useApi(
    () => api.get("/api/v1/energy/estimate", {
      query: {
        start: toISODate(new Date(Date.now() - 30 * 86400_000)),
        end: toISODate(new Date()),
      },
    }),
    ready && configured, [rev, configured]);
  const refresh = () => setRev((r) => r + 1);

  const days = useMemo(
    () => (estimate.data?.days ?? []).map((d) => ({ ...d, kwh: d.wh / 1000 })),
    [estimate.data],
  );
  const r = report.data;

  return (
    <div>
      <PageHeader
        title="Energy"
        description="Meter readings, tariffs and what the month is shaping up to cost."
        actions={
          <>
            <Button variant="secondary" onClick={() => setAddingTariff(true)}><Plus /> Add tariff</Button>
            <Button variant="primary" onClick={() => setAddingReading(true)}><Plus /> Add reading</Button>
          </>
        }
      />

      {config.data && !configured && (
        <div className="mb-4 rounded-xl border border-info/25 bg-info-soft px-4 py-3 text-[13.5px] text-fg">
          <b className="font-semibold">Set up energy tracking:</b> link your meter entity in Home Assistant or add readings and a tariff manually — the report fills itself in from there.
        </div>
      )}

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {report.loading ? (
          [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24 rounded-xl" />)
        ) : r ? (
          <>
            <Stat className="rounded-xl border border-border bg-surface p-4 shadow-card" label="This month so far"
              value={<Money pence={r.total_pence} />}
              hint={`${formatDayMonth(r.month_start)} – ${formatDayMonth(r.through)}`} />
            <Stat className="rounded-xl border border-border bg-surface p-4 shadow-card" label="Projected month"
              value={r.projected_total_pence !== null ? <Money pence={r.projected_total_pence} /> : "—"}
              hint={r.budget_pence !== null ? `budget ${formatPence(r.budget_pence)}` : "no budget set"} />
            <Stat className="rounded-xl border border-border bg-surface p-4 shadow-card" label="Daily average"
              value={r.avg_daily_wh !== null ? formatKwh(r.avg_daily_wh) : "—"}
              hint={r.missing_days ? `${r.missing_days} days unpriced` : "all days priced"} />
            <Stat className="rounded-xl border border-border bg-surface p-4 shadow-card" label="Budget position"
              value={r.projected_variance_pence !== null ? <Money pence={r.projected_variance_pence} signed tone="variance" /> : "—"}
              hint={r.projected_variance_pence !== null ? "projected vs budget" : "set a budget to compare"} />
          </>
        ) : null}
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <Panel title="Daily use — last 30 days" description="kWh per day from meter readings">
          {estimate.loading ? (
            <Skeleton className="h-64 w-full" />
          ) : days.length === 0 ? (
            <EmptyState compact icon={Gauge} title="No readings yet"
              description="Add meter readings to chart daily consumption." />
          ) : (
            <div role="img" aria-label="Daily electricity use over the last 30 days" className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={days} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                  <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeDasharray="3 4" />
                  <XAxis dataKey="date" tickFormatter={formatDayMonth} tickLine={false} axisLine={false} minTickGap={40} tick={{ fill: "var(--fg-subtle)", fontSize: 11.5 }} dy={6} />
                  <YAxis tickLine={false} axisLine={false} width={40} tick={{ fill: "var(--fg-subtle)", fontSize: 11.5 }} />
                  <Tooltip
                    cursor={{ fill: "var(--surface-3)" }}
                    content={({ active, payload }) => {
                      const d = payload?.[0]?.payload;
                      if (!active || !d) return null;
                      return (
                        <div className="rounded-lg border border-border bg-surface px-3 py-2 text-[12.5px] shadow-lg">
                          <p className="text-fg-subtle">{formatSmartDate(d.date)}</p>
                          <p className="tnum font-semibold">{formatKwh(d.wh)} · {formatPence(d.unit_pence + d.standing_pence)}</p>
                        </div>
                      );
                    }}
                  />
                  <Bar dataKey="kwh" fill="var(--chart-line)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Panel>

        <Panel flush title="Tariffs" description="Unit rate history"
          actions={<Badge tone="neutral">{tariffs.data?.length ?? 0}</Badge>}>
          {(tariffs.data ?? []).length === 0 ? (
            <EmptyState compact icon={Zap} title="No tariffs"
              description="Add your unit rate and standing charge." />
          ) : (
            <ul className="divide-y divide-border">
              {(tariffs.data ?? []).map((t: Tariff) => (
                <li key={t.id} className="px-4 py-3">
                  <p className="text-[13.5px] font-medium">
                    {formatUnitRate(t.unit_rate_millipence)}p/kWh
                  </p>
                  <p className="text-[12px] text-fg-subtle">
                    + {formatPence(t.standing_charge_pence)}/day standing · from {formatSmartDate(t.effective_from)}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <AddReadingDialog open={addingReading} onOpenChange={setAddingReading} onSaved={refresh} />
      <AddTariffDialog open={addingTariff} onOpenChange={setAddingTariff} onSaved={refresh} />
    </div>
  );
}

function AddReadingDialog({ open, onOpenChange, onSaved }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const [date, setDate] = useState(toISODate(new Date()));
  const [reading, setReading] = useState("");
  const [busy, setBusy] = useState(false);
  const valid = /^\d+(\.\d+)?$/.test(reading);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    api.post("/api/v1/energy/readings", {
      body: { date, wh: Math.round(Number(reading) * 1000), source: "manual" },
    })
      .then(() => { toast.success("Reading saved"); setReading(""); onSaved(); onOpenChange(false); })
      .catch((err: Error) => { toast.error(err.message); setBusy(false); });
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Add meter reading"
      description="Cumulative meter total in kWh — daily use is derived between readings."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-reading" disabled={!valid || busy}>{busy ? "Saving…" : "Save reading"}</Button>
      </>}>
      <form id="ff-reading" onSubmit={submit} className="grid gap-4">
        <Field label="Meter reading (kWh)">{(id) => <Input id={id} autoFocus inputMode="decimal" placeholder="e.g. 18234.5" value={reading} onChange={(e) => setReading(e.target.value.trim())} />}</Field>
        <Field label="Date">{(id) => <Input id={id} type="date" value={date} onChange={(e) => setDate(e.target.value)} />}</Field>
      </form>
    </Dialog>
  );
}

function AddTariffDialog({ open, onOpenChange, onSaved }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const [from, setFrom] = useState(toISODate(new Date()));
  const [rate, setRate] = useState("");
  const [standing, setStanding] = useState("");
  const [busy, setBusy] = useState(false);
  const valid = /^\d+(\.\d+)?$/.test(rate) && /^\d+(\.\d{1,2})?$/.test(standing);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    api.post("/api/v1/energy/tariffs", {
      body: {
        effective_from: from,
        unit_rate_millipence: Math.round(Number(rate) * 1000),
        standing_charge: standing,
      },
    })
      .then(() => { toast.success("Tariff saved"); setRate(""); setStanding(""); onSaved(); onOpenChange(false); })
      .catch((err: Error) => { toast.error(err.message); setBusy(false); });
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Add tariff"
      description="From your bill — unit rate in pence per kWh, standing charge in £ per day."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" type="submit" form="ff-tariff" disabled={!valid || busy}>{busy ? "Saving…" : "Save tariff"}</Button>
      </>}>
      <form id="ff-tariff" onSubmit={submit} className="grid gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Unit rate (pence/kWh)" hint="e.g. 24.5">{(id) => <Input id={id} inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value.trim())} />}</Field>
          <Field label="Standing charge (£/day)" hint="e.g. 0.53">{(id) => <Input id={id} inputMode="decimal" value={standing} onChange={(e) => setStanding(e.target.value.trim())} />}</Field>
        </div>
        <Field label="Effective from">{(id) => <Input id={id} type="date" value={from} onChange={(e) => setFrom(e.target.value)} />}</Field>
      </form>
    </Dialog>
  );
}
