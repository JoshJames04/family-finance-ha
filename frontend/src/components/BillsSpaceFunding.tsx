import { useState } from "react";
import { toast } from "sonner";
import { api } from "../api/client";
import { useAccounts, useReady } from "../api/hooks";
import { useApi } from "../api/useApi";
import { formatDate, toISODate } from "../lib/format";
import { Button } from "./Button";
import { DataView } from "./DataView";
import { Field, Input, Select } from "./Field";
import { Money } from "./Money";
import { Panel } from "./Panel";

export function BillsSpaceFunding() {
  const ready = useReady();
  const accounts = useAccounts();
  const [target, setTarget] = useState<number>();
  const [source, setSource] = useState<number>();
  const [through, setThrough] = useState("");
  const [buffer, setBuffer] = useState("0");
  const [busy, setBusy] = useState(false);
  const [standingAmount, setStandingAmount] = useState("");
  const bills = accounts.data?.items.filter(a => a.owner_profile_id === null && a.type === "bills") ?? [];
  const selected = bills.find(a => a.id === target) ?? bills[0];
  const sources = accounts.data?.items.filter(a => a.owner_profile_id === null && a.type === "joint") ?? [];
  const sourceAccount = sources.find(a => a.id === source) ?? (sources.length === 1 ? sources[0] : undefined);
  const bufferPence = Math.round(Number(buffer) * 100);
  const validBuffer = Number.isFinite(bufferPence) && bufferPence >= 0 && bufferPence <= 100_000_000;
  const funding = useApi(() => api.get("/api/v1/transfer-rules/bills-space/{account_id}", {
    path: { account_id: selected!.id },
    query: { through: through || undefined, buffer_pence: bufferPence },
  }), ready && selected != null && validBuffer, [selected?.id, through, bufferPence]);

  const approvals = useApi(() => api.get("/api/v1/transfer-rules/shared-split/{account_id}/proposals", {
    path: { account_id: selected!.id },
  }), ready && selected != null, [selected?.id]);

  if (!selected) return null;
  const save = async () => {
    if (!sourceAccount || !funding.data || funding.loading || !validBuffer) return;
    setBusy(true);
    try {
      await api.post("/api/v1/transfer-rules/bills-space/{account_id}", {
        path: { account_id: selected.id },
        body: { source_account_id: sourceAccount.id, through: through || undefined,
          buffer_pence: bufferPence, amount_pence: funding.data.top_up_pence },
      });
      toast.success("One-off top-up added to the forecast");
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      funding.refetch();
      setBusy(false);
    }
  };
  const propose = async (ruleId: number) => {
    setBusy(true);
    try {
      const change = await api.post("/api/v1/transfer-rules/bills-space/{account_id}/standing", {
        path: { account_id: selected.id },
        body: { rule_id: ruleId, amount_pence: Math.round(Number(standingAmount) * 100) },
      });
      setStandingAmount("");
      toast.success(change.state === "accepted" ? "Standing top-up updated" : "Sent for approval");
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      approvals.refetch();
      funding.refetch();
      setBusy(false);
    }
  };
  const decide = async (id: string, decision: "accept" | "keep") => {
    setBusy(true);
    try {
      await api.post("/api/v1/transfer-rules/shared-split/{account_id}/proposals/{proposal_id}/{decision}", {
        path: { account_id: selected.id, proposal_id: id, decision },
      });
      toast.success(decision === "accept" ? "Standing top-up updated" : "Kept the existing top-up");
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      approvals.refetch();
      funding.refetch();
      setBusy(false);
    }
  };
  return (
    <Panel className="mt-4 space-y-3 p-4">
      <h2 className="text-sm font-semibold">Bills account funding</h2>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Bills account">{id => (
          <Select id={id} value={selected.id} onChange={e => { setTarget(Number(e.target.value)); setStandingAmount(""); }}>
            {bills.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Select>
        )}</Field>
        <Field label="Cover bills through" hint="Blank uses the next scheduled top-up or 30 days.">{id => (
          <Input id={id} type="date" value={through} min={toISODate(new Date())}
            onChange={e => setThrough(e.target.value)} />
        )}</Field>
        <Field label="Keep a buffer (£)">{id => (
          <Input id={id} type="number" min="0" max="1000000" step="0.01" value={buffer}
            onChange={e => setBuffer(e.target.value)} />
        )}</Field>
      </div>
      {validBuffer && <DataView state={funding} empty="Funding estimate unavailable">{result => (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
            <span>Suggested top-up <Money pence={result.top_up_pence} className="font-semibold" /></span>
            <span>Available <Money pence={result.balance_pence} /></span>
            <span>Through {formatDate(result.through)}</span>
            {result.first_shortfall && <span>First shortfall {formatDate(result.first_shortfall)}</span>}
          </div>
          <p className="text-xs text-fg-subtle">
            Bills are counted before incoming transfers on the same day. A one-off top-up is planned before those bills.
          </p>
          {!!result.movements.length && <details className="text-sm">
            <summary className="cursor-pointer">Bills and transfers in this estimate</summary>
            <ul className="mt-2 space-y-1">
              {result.movements.map((row, index) => (
                <li key={index} className="flex flex-wrap justify-between gap-x-3">
                  <span>{formatDate(row.date)} · {row.label}</span><Money pence={row.pence} />
                </li>
              ))}
            </ul>
          </details>}
          {result.top_up_pence > 0 && sources.length > 0 && <div className="flex flex-wrap items-end gap-3">
            <Field label="Fund from">{id => (
              <Select id={id} value={sourceAccount?.id ?? ""} onChange={e => setSource(Number(e.target.value))}>
                <option value="" disabled>Select a joint account</option>
                {sources.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
            )}</Field>
            <Button type="button" disabled={busy || funding.loading || !sourceAccount} onClick={() => void save()}>
              Plan one-off top-up
            </Button>
            <span className="text-xs text-fg-subtle">Adds a forecast entry. Your standing transfer stays as agreed.</span>
          </div>}
        </div>
      )}</DataView>}
    </Panel>
  );
}
