/** Review inbox sources: unusual-transaction flags, expected↔actual
 * matches and detected subscriptions — everything that waits on a
 * human decision, counted in one place for the nav badge, the
 * dashboard callout and the Review page. */
import { api } from "./client";
import { useApi, useIdentity, type ApiState } from "./useApi";
import type { components } from "./schema";

type Anomaly = components["schemas"]["AnomalyFlagOut"];
type Recon = components["schemas"]["ReconciliationOut"];
type Candidate = components["schemas"]["DetectionCandidateOut"];
type Merge = components["schemas"]["MergeCandidateOut"];

export interface ReviewData {
  anomalies: ApiState<Anomaly[]>;
  recon: ApiState<Recon[]>;
  candidates: ApiState<Candidate[]>;
  merges: ApiState<Merge[]>;
  total: number;
  loading: boolean;
}

export function useReview(rev = 0): ReviewData {
  const ready = useIdentity().data !== null;
  const anomalies = useApi(() => api.get("/api/v1/anomalies/queue"), ready, [rev]);
  const recon = useApi(() => api.get("/api/v1/reconcile/queue"), ready, [rev]);
  const candidates = useApi(() => api.get("/api/v1/subscriptions/candidates"), ready, [rev]);
  // Bank-sync duplicates awaiting a merge decision (issue #271).
  const merges = useApi(() => api.get("/api/v1/sync/merge-candidates"), ready, [rev]);
  const pendingCandidates = (Array.isArray(candidates.data) ? candidates.data : [])
    .filter((c) => c.status === "pending");
  const list = <T,>(s: ApiState<T[]>) => (Array.isArray(s.data) ? s.data : []);
  return {
    anomalies,
    recon,
    candidates,
    merges,
    total:
      list(anomalies).filter((a) => a.status === "pending").length +
      list(recon).length +
      pendingCandidates.length +
      list(merges).filter((m) => m.status === "pending").length,
    loading: anomalies.loading || recon.loading
      || candidates.loading || merges.loading,
  };
}
