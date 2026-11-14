/** Shared read-model hooks used across pages and dashboard widgets. */
import { useMemo } from "react";
import { api } from "./client";
import { useApi, useIdentity } from "./useApi";
import type { components } from "./schema";
import { toISODate } from "../lib/format";

export type Budget = components["schemas"]["BudgetOut"];
export type Category = components["schemas"]["CategoryOut"];

/** Household + member identity resolved → safe to fire scoped calls. */
export function useReady(): boolean {
  const identity = useIdentity();
  return identity.data !== null || (identity.error !== null && !identity.loading);
}

export function useAccounts(rev = 0) {
  return useApi(() => api.get("/api/v1/accounts"), useReady(), [rev], []);
}

export function useCategories() {
  const state = useApi(
    () => api.get("/api/v1/categories"), useReady());
  const byId = useMemo(() => {
    const m = new Map<number, Category>();
    for (const c of state.data?.items ?? []) m.set(c.id, c);
    return m;
  }, [state.data]);
  const data = useMemo(() => state.data ? { ...state.data,
    items: [...state.data.items].sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: "base" }) || a.id - b.id),
  } : null, [state.data]);
  return { ...state, data, byId };
}

/** Projection for the first (primary) account unless one is given. */
export function useProjection(accountId?: number | null) {
  const accounts = useAccounts();
  const id = accountId ?? accounts.data?.items.find((a) => !a.archived)?.id
    ?? accounts.data?.items[0]?.id ?? null;
  const projection = useApi(
    () => api.get("/api/v1/accounts/{account_id}/projection",
      { path: { account_id: id! } }),
    id !== null, [id]);
  const accountName = accounts.data?.items.find((a) => a.id === id)?.name;
  // No account at all → behave as an empty (not loading) state.
  if (accounts.data && id === null) {
    return { accountName, data: null, loading: false, retrying: false,
             error: null, refetch: projection.refetch };
  }
  if (accounts.loading || accounts.error) {
    return { accountName, data: null, loading: accounts.loading,
             retrying: accounts.retrying, error: accounts.error,
             refetch: accounts.refetch };
  }
  if (projection.data && projection.data.account_id !== id) {
    return { ...projection, accountName, data: null, loading: !projection.error };
  }
  return { ...projection, accountName };
}

/** Expected movements from today for `days` days. */
export function useUpcoming(days = 90, rev = 0) {
  const range = useMemo(() => {
    const start = new Date();
    const end = new Date();
    end.setDate(end.getDate() + days);
    return { start: toISODate(start), end: toISODate(end) };
  }, [days]);
  return useApi(
    () => api.get("/api/v1/expected-movements", { query: range }),
    useReady(), [range.start, range.end, rev]);
}

/** Budget whose period contains `on` — shared budgets preferred. */
export function pickBudgetFor(items: Budget[], on: Date = new Date()): Budget | undefined {
  const iso = toISODate(on);
  const covering = items.filter((b) => b.start_date <= iso && iso <= b.end_date);
  return covering.find((b) => b.owner_profile_id == null) ?? covering[0];
}

export function useBudgets() {
  return useApi(() => api.get("/api/v1/budgets"), useReady());
}

export interface LineAnalysis {
  original_pence: number;
  revised_pence: number | null;
  revision_reason: string | null;
  effective_pence: number;
  actual_pence: number;
  variance_pence: number;
  accuracy_pct: number;
  original_accuracy_pct: number;
}

export function useBudgetDetail(id: number | null, rev = 0, basis: "calendar" | "pay_cycle" = "calendar") {
  const detailRequest = useApi(
    () => api.get("/api/v1/budgets/{budget_id}", { path: { budget_id: id! }, query: { basis } })
      .then((value) => ({ id, basis, value })),
    id !== null, [id, rev, basis]);
  const actualsRequest = useApi(
    () => api.get("/api/v1/budgets/{budget_id}/actuals", { path: { budget_id: id! }, query: { basis } })
      .then((value) => ({ id, basis, value })),
    id !== null, [id, rev, basis]);
  // Retain stale values only for the same budget; another owner/period's
  // completed response cannot be relabelled while the new one is pending.
  const detail = {
    ...detailRequest,
    data: detailRequest.data?.id === id && detailRequest.data.basis === basis ? detailRequest.data.value : null,
    loading: detailRequest.loading || ((detailRequest.data?.id !== id || detailRequest.data?.basis !== basis) && !detailRequest.error),
  };
  const actuals = {
    ...actualsRequest,
    data: actualsRequest.data?.id === id && actualsRequest.data.basis === basis ? actualsRequest.data.value : null,
    loading: actualsRequest.loading || ((actualsRequest.data?.id !== id || actualsRequest.data?.basis !== basis) && !actualsRequest.error),
  };
  const analyses = (actuals.data?.lines ?? {}) as unknown as Record<string, LineAnalysis>;
  return { detail, actuals, analyses };
}

export function useMonthlyReport(year: number, month: number, rev = 0) {
  return useApi(
    () => api.get("/api/v1/reports/monthly", { query: { year, month } }),
    useReady(), [year, month, rev]);
}

export type Report = components["schemas"]["ReportOut"];

/** Pull a figure (by key) out of a ReportOut section. */
export function reportFigure(r: Report | null | undefined, section: string, key: string) {
  return r?.sections?.find((s) => s.key === section)?.figures?.find((f) => f.key === key);
}

/** Total of every figure in a section (e.g. spend by category). */
export function reportSectionTotal(r: Report | null | undefined, section: string): number {
  return (r?.sections?.find((s) => s.key === section)?.figures ?? [])
    .reduce((s, f) => s + Math.abs(f.amount_pence ?? 0), 0);
}
