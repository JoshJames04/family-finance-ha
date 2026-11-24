import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, getHouseholdId, getProfileId } from "../api/client";
import { useAccounts, useProjection } from "../api/hooks";
import { useApi, useIdentity } from "../api/useApi";
import type { components } from "../api/schema";
import { isIngress } from "../ingress";
import { toISODate } from "../lib/format";

type Account = components["schemas"]["AccountOut"];
type Employment = components["schemas"]["EmploymentOut"];
interface Selection {
  accounts: Account[];
  accountId: number | null;
  select: (id: number | null) => void;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}
const AccountContext = createContext<Selection | null>(null);
export const useDashboardAccount = () => useContext(AccountContext);

export function useSalaryAccountSelection(enabled = true): Selection {
  const identity = useIdentity();
  const ingress = isIngress();
  const ready = enabled && identity.data !== null;
  const me = useApi(() => api.get("/api/v1/auth/me"), ready && ingress);
  const profileId = ingress ? me.data?.id ?? null : ready ? getProfileId() : null;
  const accounts = useAccounts();
  const jobs = useApi(async () => {
    const incomplete = "Employment list is incomplete. Retry loading salary settings.";
    const items: Employment[] = [];
    for (let page = 1; ; page++) {
      const result = await api.get("/api/v1/employments", { query: { page, size: 200 } });
      if (!Array.isArray(result.items) || !Number.isSafeInteger(result.total) || result.total < 0) {
        throw new Error(incomplete);
      }
      items.push(...result.items);
      if (items.length >= result.total) return { ...result, items };
      if (result.items.length === 0) throw new Error(incomplete);
    }
  }, ready && profileId !== null, [profileId]);
  const scope = `${getHouseholdId()}:${profileId}`;
  const [manual, setManual] = useState<{ scope: string; id: number } | null>(null);
  useEffect(() => {
    if (manual && (manual.scope !== scope || (accounts.data && !accounts.loading
      && !accounts.data.items.some(a => a.id === manual.id && !a.archived)))) setManual(null);
  }, [scope, accounts.data, accounts.loading, manual]);
  const active = accounts.data?.items.filter(a => !a.archived) ?? [];
  const available = new Set(active.map(a => a.id));
  const today = toISODate(new Date());
  const salaryAccounts = new Set((jobs.data?.items ?? [])
    .filter(e => e.profile_id === profileId && e.is_active
      && (!e.start_date || e.start_date <= today) && (!e.end_date || e.end_date >= today)
      && e.paid_into_account_id !== null && available.has(e.paid_into_account_id))
    .map(e => e.paid_into_account_id!));
  const error = identity.error ?? (ingress ? me.error : null) ?? accounts.error ?? jobs.error;
  const loading = enabled && !error && (!ready || accounts.loading || (ingress && me.loading)
    || (profileId !== null && jobs.loading));
  const selected = manual?.scope === scope && available.has(manual.id) ? manual.id : null;
  const accountId = loading || error ? null
    : selected ?? (salaryAccounts.size === 1 ? [...salaryAccounts][0]! : null);
  const refetch = () => {
    identity.refetch(); accounts.refetch();
    if (ingress) me.refetch();
    jobs.refetch();
  };
  return { accounts: active, accountId, loading, error, refetch,
    select: id => setManual(id === null ? null : { scope, id }) };
}

export function DashboardAccountProvider({ children, enabled = true }: { children: ReactNode; enabled?: boolean }) {
  const selection = useSalaryAccountSelection(enabled);
  return <AccountContext.Provider value={selection}>{children}</AccountContext.Provider>;
}

/** Outside Dashboard, independently rendered widgets retain their existing default. */
export function useDashboardProjection(legacyAccountId?: number | null) {
  const selection = useDashboardAccount();
  const projection = useProjection(selection ? selection.accountId : legacyAccountId,
    { autoSelect: selection === null });
  return {
    ...projection,
    loading: selection?.error ? false : selection?.loading || projection.loading,
    error: selection?.error ?? projection.error,
    refetch: selection?.error ? selection.refetch : projection.refetch,
  };
}
