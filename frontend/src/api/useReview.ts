/** Review inbox sources: migration flags, unusual-transaction flags,
 * expected↔actual matches and detected subscriptions — everything that
 * waits on a human decision, counted in one place for the nav badge,
 * the dashboard callout and the Review page. */
import { api } from "./client";
import { useApi, useIdentity, type ApiState } from "./useApi";
import type { components } from "./schema";

type Flag = components["schemas"]["FlagOut"];
type Anomaly = components["schemas"]["AnomalyFlagOut"];
type Recon = components["schemas"]["ReconciliationOut"];
type Candidate = components["schemas"]["DetectionCandidateOut"];
type Merge = components["schemas"]["MergeCandidateOut"];

const CLOSED_FLAG = new Set(["resolved", "wont_fix"]);

export interface ReviewData {
  flags: ApiState<Flag[]>;
  anomalies: ApiState<Anomaly[]>;
  recon: ApiState<Recon[]>;
  candidates: ApiState<Candidate[]>;
  merges: ApiState<Merge[]>;
  openFlags: Flag[];
  total: number;
  loading: boolean;
}

export function useReview(rev = 0): ReviewData {
  const ready = useIdentity().data !== null;
  const flags = useApi(() => api.get("/api/v1/migration/flags"), ready, [rev]);
  const anomalies = useApi(() => api.get("/api/v1/anomalies/queue"), ready, [rev]);
  const recon = useApi(() => api.get("/api/v1/reconcile/queue"), ready, [rev]);
  const candidates = useApi(() => api.get("/api/v1/subscriptions/candidates"), ready, [rev]);
  // Bank-sync duplicates awaiting a merge decision (issue #271).
  const merges = useApi(() => api.get("/api/v1/sync/merge-candidates"), ready, [rev]);
  const openFlags = (Array.isArray(flags.data) ? flags.data : [])
    .filter((f) => !CLOSED_FLAG.has(f.status));
  const pendingCandidates = (Array.isArray(candidates.data) ? candidates.data : [])
    .filter((c) => c.status === "pending");
  const list = <T,>(s: ApiState<T[]>) => (Array.isArray(s.data) ? s.data : []);
  return {
    flags,
    anomalies,
    recon,
    candidates,
    merges,
    openFlags,
    total:
      openFlags.length +
      list(anomalies).filter((a) => a.status === "pending").length +
      list(recon).length +
      pendingCandidates.length +
      list(merges).filter((m) => m.status === "pending").length,
    loading: flags.loading || anomalies.loading || recon.loading
      || candidates.loading || merges.loading,
  };
}
