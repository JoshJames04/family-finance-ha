/**
 * Data hooks for the shell pages: `useHouseholds` resolves the active
 * household (first one, persisted), `useApi` runs a typed request and
 * exposes loading/error/data states.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, getHouseholdId, getProfileId, setHouseholdId, setProfileId }
  from "./client";
import type { components } from "./schema";

export type Profile = components["schemas"]["ProfileOut"];

export interface ApiState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

/** Fetch households, persist the first as active, expose the id. */
export function useHouseholds(): ApiState<{ id: number; name: string }[]> {
  const [state, setState] = useState<ApiState<{ id: number; name: string }[]>>({
    data: null,
    loading: true,
    error: null,
  });
  useEffect(() => {
    let cancelled = false;
    api
      .get("/api/v1/households")
      .then((page) => {
        if (cancelled) return;
        const items = page.items;
        if (items.length > 0 && getHouseholdId() === null) {
          setHouseholdId(items[0]!.id);
        }
        setState({ data: items, loading: false, error: null });
      })
      .catch((e: Error) => {
        if (!cancelled) {
          setState({ data: null, loading: false, error: e.message });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return state;
}

/**
 * Member identity for standalone mode: after the household resolves,
 * load its profiles and pick a default (stored choice if still active,
 * else the first admin) so `useReady`-gated calls carry `X-Profile-Id`
 * on their first request. Under ingress the header is ignored — HA
 * injects identity — so this costs one harmless request.
 */
export function useIdentity(): ApiState<Profile[]> {
  const households = useHouseholds();
  const state = useApi(
    () =>
      api
        .get("/api/v1/profiles")
        .then((page) => {
          const items = page.items;
          const stored = getProfileId();
          const stillValid = items.some(
            (p) => p.id === stored && p.is_active);
          if (!stillValid) {
            const fallback = items.find((p) => p.is_active && p.role === "admin")
              ?? items.find((p) => p.is_active);
            setProfileId(fallback?.id ?? null);
          }
          return items;
        })
        // Identity is optional (fresh installs have no profiles) — a
        // failed probe must never block readiness.
        .catch(() => [] as Profile[]),
    households.data !== null,
    [households.data?.length],
  );
  return state;
}

/**
 * Run a typed API call once `ready` is true (typically once the
 * household id is known). Re-runs when `ready` flips or `deps` change.
 */
export function useApi<T>(
  fetcher: () => Promise<T>,
  ready: boolean,
  deps: readonly unknown[] = [],
): ApiState<T> {
  const [state, setState] = useState<ApiState<T>>({
    data: null,
    loading: ready,
    error: null,
  });
  const fnRef = useRef(fetcher);
  fnRef.current = fetcher;
  const load = useCallback(() => {
    setState((s) => ({ ...s, loading: true, error: null }));
    fnRef
      .current()
      .then((data) => setState({ data, loading: false, error: null }))
      .catch((e: Error) =>
        setState({ data: null, loading: false, error: e.message }),
      );
  }, []);
  useEffect(() => {
    if (ready) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, load, ...deps]);
  return state;
}
