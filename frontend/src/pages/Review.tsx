/** Review inbox: everything waiting on a human decision — unusual
 * transactions, expected↔actual matches and detected subscriptions —
 * worked through in one place. */
import { useState } from "react";
import { toast } from "sonner";
import {
  BadgeCheck, Check, CheckCheck, GitMerge, Radar, Repeat, Sparkles,
} from "lucide-react";
import { api } from "../api/client";
import { useReview } from "../api/useReview";
import type { components } from "../api/schema";
import { Panel } from "../components/Panel";
import { Badge } from "../components/Badge";
import { Money } from "../components/Money";
import { Button } from "../components/Button";
import { IconTile, PageHeader } from "../components/Display";
import { EmptyState, Skeleton } from "../components/Feedback";
import { formatDateTime, formatSmartDate } from "../lib/format";

type Anomaly = components["schemas"]["AnomalyFlagOut"];
type Recon = components["schemas"]["ReconciliationOut"];
type Candidate = components["schemas"]["DetectionCandidateOut"];
type Merge = components["schemas"]["MergeCandidateOut"];

/** A sync merge candidate's `incoming` is the raw provider feed item —
 * pull out a readable payee/amount/date without guessing at schema. */
function mergeSummary(m: Merge): { title: string; pence: number | null; when: string | null } {
  const inc = m.incoming as Record<string, unknown>;
  const title =
    [inc.counterPartyName, inc.counterParty, inc.reference, inc.description, inc.type]
      .find((v): v is string => typeof v === "string" && v.length > 0) ?? "Bank item";
  const amt = inc.amount;
  let pence: number | null = null;
  if (amt && typeof amt === "object" && "minorUnits" in amt) {
    const minor = (amt as { minorUnits?: unknown }).minorUnits;
    if (typeof minor === "number") {
      pence = inc.direction === "IN" ? minor : -minor;
    }
  }
  const when =
    [inc.transactionTime, inc.transaction_time, inc.date]
      .find((v): v is string => typeof v === "string" && v.length > 0) ?? null;
  return { title, pence, when };
}

export function Review() {
  const [rev, setRev] = useState(0);
  const review = useReview(rev);
  const refresh = () => setRev((r) => r + 1);
  const act = (p: Promise<unknown>, msg: string) =>
    p.then(() => { toast.success(msg); refresh(); })
      .catch((e: Error) => toast.error(e.message));

  const anomalies = (Array.isArray(review.anomalies.data) ? review.anomalies.data : [])
    .filter((a) => a.status === "pending");
  const recon = Array.isArray(review.recon.data) ? review.recon.data : [];
  const candidates = (Array.isArray(review.candidates.data) ? review.candidates.data : [])
    .filter((c) => c.status === "pending");
  const merges = (Array.isArray(review.merges.data) ? review.merges.data : [])
    .filter((m) => m.status === "pending");

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
            description="Nothing needs your attention. Unusual transactions, match suggestions and detected subscriptions land here when they need a decision."
          />
        </Panel>
      ) : (
        <div className="flex flex-col gap-4">
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

          {merges.length > 0 && (
            <Panel
              title={`Possible duplicates (${merges.length})`}
              description="Bank-synced items that may already exist in your ledger — merge or keep both."
              flush
            >
              <ul className="divide-y divide-border">
                {merges.map((m) => {
                  const s = mergeSummary(m);
                  return (
                    <li key={m.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
                      <IconTile icon={GitMerge} label={s.title} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-[13.5px] font-medium">{s.title}</p>
                        <p className="text-[12px] text-fg-subtle">
                          Synced {s.when ? formatSmartDate(s.when.slice(0, 10)) : formatDateTime(m.created_at)}
                          {" · matches transaction #"}{m.existing_txn_id}
                        </p>
                      </div>
                      {s.pence != null && <Money pence={s.pence} signed tone="auto" className="font-medium" />}
                      <div className="flex gap-1.5">
                        <Button size="sm" variant="primary"
                          onClick={() => act(api.post("/api/v1/sync/merge-candidates/{cid}/resolve", { path: { cid: m.id }, body: { action: "merged" } }), "Merged — one kept")}>
                          <Check /> Merge
                        </Button>
                        <Button size="sm" variant="ghost"
                          onClick={() => act(api.post("/api/v1/sync/merge-candidates/{cid}/resolve", { path: { cid: m.id }, body: { action: "dismissed" } }), "Kept both")}>
                          Keep both
                        </Button>
                      </div>
                    </li>
                  );
                })}
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

    </div>
  );
}
