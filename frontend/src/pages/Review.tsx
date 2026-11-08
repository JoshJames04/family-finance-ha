/** Review inbox: everything waiting on a human decision — migration
 * flags, unusual transactions, expected↔actual matches and detected
 * subscriptions — worked through in one place. */
import { useState } from "react";
import { toast } from "sonner";
import {
  BadgeCheck, Check, CheckCheck, FileWarning, Radar, Repeat, Sparkles,
} from "lucide-react";
import { api } from "../api/client";
import { useReview } from "../api/useReview";
import type { components } from "../api/schema";
import { Panel } from "../components/Panel";
import { Badge } from "../components/Badge";
import { Money } from "../components/Money";
import { Button } from "../components/Button";
import { Dialog } from "../components/Dialog";
import { Field, Select, Textarea } from "../components/Field";
import { IconTile, PageHeader } from "../components/Display";
import { EmptyState, Skeleton } from "../components/Feedback";
import { formatDateTime, formatGBP, formatSmartDate } from "../lib/format";

type Flag = components["schemas"]["FlagOut"];
type Anomaly = components["schemas"]["AnomalyFlagOut"];
type Recon = components["schemas"]["ReconciliationOut"];
type Candidate = components["schemas"]["DetectionCandidateOut"];

/** Workbook cell coordinates read "B16"-style (col index 3 → C). */
function cellRef(col: unknown, row: unknown): string | null {
  if (typeof col !== "number" || typeof row !== "number") return null;
  return `${String.fromCharCode(64 + col)}${row}`;
}

const DETAIL_LABELS: Record<string, string> = {
  last_good_value: "last good",
  reconstructed_value: "reconstructed",
  residual: "residual",
  period_delta: "period delta",
  last_good_row: "last good at row",
};

/**
 * Migration flags carry a JSON detail blob — decode it into something
 * readable ("Family Loan Ledger · cell C16" + labelled figures). Workbook
 * amounts are stored in pounds, so they render via formatGBP.
 */
function flagSummary(f: Flag): { title: string; description: string } {
  const fallback = {
    title: `Defect ${f.defect_id}`,
    description: f.detail ?? "No detail recorded.",
  };
  try {
    const d = JSON.parse(f.detail ?? "{}") as Record<string, unknown>;
    const sheet = typeof d.sheet === "string" ? d.sheet : null;
    const cell = cellRef(d.col, d.cell_row);
    const figures = Object.entries(d)
      .filter(([k, v]) => DETAIL_LABELS[k] !== undefined && typeof v === "number")
      .map(([k, v]) =>
        k === "last_good_row"
          ? `${DETAIL_LABELS[k]} ${v}`
          : `${DETAIL_LABELS[k]} ${formatGBP(v as number)}`);
    return {
      title: sheet
        ? `${sheet}${cell ? ` · cell ${cell}` : ""}`
        : `Defect ${f.defect_id}`,
      description: figures.length > 0
        ? figures.join("  ·  ")
        : fallback.description,
    };
  } catch {
    return fallback;
  }
}

export function Review() {
  const [rev, setRev] = useState(0);
  const review = useReview(rev);
  const [resolving, setResolving] = useState<Flag | null>(null);
  const refresh = () => setRev((r) => r + 1);
  const act = (p: Promise<unknown>, msg: string) =>
    p.then(() => { toast.success(msg); refresh(); })
      .catch((e: Error) => toast.error(e.message));

  const anomalies = (Array.isArray(review.anomalies.data) ? review.anomalies.data : [])
    .filter((a) => a.status === "pending");
  const recon = Array.isArray(review.recon.data) ? review.recon.data : [];
  const candidates = (Array.isArray(review.candidates.data) ? review.candidates.data : [])
    .filter((c) => c.status === "pending");

  return (
    <div>
      <PageHeader
        title="Review"
        description="Items that need a decision — work top to bottom and this clears itself."
        actions={review.total > 0
          ? <Badge tone="accent" className="!h-7 !px-3 !text-[13px]">{review.total} waiting</Badge>
          : undefined}
      />

      {review.loading ? (
        <div className="flex flex-col gap-4">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-28 rounded-xl" />)}
        </div>
      ) : review.total === 0 ? (
        <Panel>
          <EmptyState
            icon={CheckCheck}
            title="All caught up"
            description="Nothing needs your attention. Migration flags, unusual transactions, match suggestions and detected subscriptions land here when they need a decision."
          />
        </Panel>
      ) : (
        <div className="flex flex-col gap-4">
          {review.openFlags.length > 0 && (
            <Panel
              title={`Migration review (${review.openFlags.length})`}
              description="Flags raised when the workbook was imported — confirm each one is right."
              flush
            >
              <ul className="divide-y divide-border">
                {review.openFlags.map((f) => {
                  const s = flagSummary(f);
                  return (
                    <li key={f.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
                      <IconTile icon={FileWarning} label="migration flag" />
                      <div className="min-w-0 flex-1">
                        <p className="text-[13.5px] font-medium">{s.title}</p>
                        <p className="text-[12.5px] text-fg-subtle">{s.description}</p>
                      </div>
                      <Badge tone="warning">{f.status.replace(/_/g, " ")}</Badge>
                      <Button size="sm" variant="primary" onClick={() => setResolving(f)}>
                        Review
                      </Button>
                    </li>
                  );
                })}
              </ul>
            </Panel>
          )}

          {anomalies.length > 0 && (
            <Panel
              title={`Unusual transactions (${anomalies.length})`}
              description="Statistically odd items spotted on your accounts — confirm they're genuine."
              flush
            >
              <ul className="divide-y divide-border">
                {anomalies.map((a) => (
                  <li key={a.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
                    <IconTile icon={Radar} label={a.trigger} />
                    <div className="min-w-0 flex-1">
                      <p className="text-[13.5px] font-medium">{a.trigger.replace(/_/g, " ")}</p>
                      <p className="text-[12.5px] text-fg-subtle">
                        Transaction #{a.transaction_id} · {formatDateTime(a.created_at)}
                      </p>
                    </div>
                    <div className="flex gap-1.5">
                      <Button size="sm" variant="secondary"
                        onClick={() => act(api.post("/api/v1/anomalies/{fid}/confirm", { path: { fid: a.id } }), "Marked as genuine")}>
                        <Check /> Genuine
                      </Button>
                      <Button size="sm" variant="ghost"
                        onClick={() => act(api.post("/api/v1/anomalies/{fid}/dismiss", { path: { fid: a.id } }), "Dismissed")}>
                        Dismiss
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          {recon.length > 0 && (
            <Panel
              title={`Suggested matches (${recon.length})`}
              description="Scheduled movements that look like they've landed — confirm to reconcile."
              flush
            >
              <ul className="divide-y divide-border">
                {recon.map((r) => (
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
                      <Button size="sm" variant="primary"
                        onClick={() => act(api.post("/api/v1/reconcile/{rid}/confirm", { path: { rid: r.id } }), "Match confirmed")}>
                        <Check /> Confirm
                      </Button>
                      <Button size="sm" variant="ghost"
                        onClick={() => act(api.post("/api/v1/reconcile/{rid}/dismiss", { path: { rid: r.id } }), "Match dismissed")}>
                        Dismiss
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          {candidates.length > 0 && (
            <Panel
              title={`Possible subscriptions (${candidates.length})`}
              description="Recurring payments spotted in your transactions — track them as subscriptions?"
              flush
            >
              <ul className="divide-y divide-border">
                {candidates.map((c) => (
                  <li key={c.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
                    <IconTile icon={Repeat} label={c.payee} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13.5px] font-medium">{c.payee}</p>
                      <p className="text-[12px] text-fg-subtle">
                        <span className="capitalize">{c.frequency}</span> · seen {c.occurrence_count}×
                        · {formatSmartDate(c.first_seen)} – {formatSmartDate(c.last_seen)}
                      </p>
                    </div>
                    <Money pence={c.expected_pence} className="font-medium" />
                    <Badge tone={c.confidence >= 70 ? "info" : "neutral"} dot={false}>
                      <Sparkles className="size-3" /> {c.confidence}%
                    </Badge>
                    <div className="flex gap-1.5">
                      <Button size="sm" variant="primary"
                        onClick={() => act(api.post("/api/v1/subscriptions/candidates/{cid}/confirm", { path: { cid: c.id }, body: {} }), `Now tracking ${c.payee}`)}>
                        <BadgeCheck /> Track it
                      </Button>
                      <Button size="sm" variant="ghost"
                        onClick={() => act(api.post("/api/v1/subscriptions/candidates/{cid}/dismiss", { path: { cid: c.id }, body: {} }), "Dismissed")}>
                        Not a subscription
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </div>
      )}

      {resolving && (
        <ResolveFlagDialog
          flag={resolving}
          onOpenChange={(o) => { if (!o) setResolving(null); }}
          onDone={refresh}
        />
      )}
    </div>
  );
}

function ResolveFlagDialog({ flag, onOpenChange, onDone }: {
  flag: Flag;
  onOpenChange: (o: boolean) => void;
  onDone: () => void;
}) {
  const [status, setStatus] = useState<"resolved" | "wont_fix">("resolved");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!note.trim()) return;
    setBusy(true);
    api.post("/api/v1/migration/flags/{flag_id}/resolve", {
      path: { flag_id: flag.id },
      body: { status, note: note.trim() },
    })
      .then(() => {
        toast.success(`Flag ${flag.defect_id} marked ${status.replace(/_/g, " ")}`);
        onDone();
        onOpenChange(false);
      })
      .catch((err: Error) => { toast.error(err.message); setBusy(false); });
  };
  return (
    <Dialog
      open
      onOpenChange={onOpenChange}
      title={`Resolve ${flag.defect_id}`}
      description="Check the figure against your records, then record the outcome — it's kept in the audit log."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" type="submit" form="ff-resolve" disabled={busy || !note.trim()}>
            {busy ? "Saving…" : "Record outcome"}
          </Button>
        </>
      }
    >
      <form id="ff-resolve" onSubmit={submit} className="grid gap-4">
        <div className="rounded-xl border border-border bg-surface-2 p-3 text-[13px] text-fg-muted">
          <p className="font-medium text-fg">{flagSummary(flag).title}</p>
          <p className="mt-0.5">{flagSummary(flag).description}</p>
        </div>
        <Field label="Outcome">
          {(id) => (
            <Select id={id} value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
              <option value="resolved">Resolved — figure is correct</option>
              <option value="wont_fix">Won't fix — accepted as-is</option>
            </Select>
          )}
        </Field>
        <Field label="Reviewer note (required)" hint="What you checked and what you decided.">
          {(id) => <Textarea id={id} required value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Checked against bank statement — balance is right." />}
        </Field>
      </form>
    </Dialog>
  );
}
