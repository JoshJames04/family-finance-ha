/**
 * Data hooks for the shell pages: `useHouseholds` resolves the active
 * household (first one, persisted), `useApi` runs a typed request and
 * exposes loading/error/data states.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { isIngress } from "../ingress";
import { api, getHouseholdId, getProfileId, setHouseholdId, setProfileId }
  from "./client";
import type { components } from "./schema";

export type Profile = components["schemas"]["ProfileOut"];

export interface ApiState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

/** Max attempts before a households failure is surfaced as a
 * terminal error — covers ~30 s of backend restart behind the
 * add-on/ingress proxy (issue #430). */
const HOUSEHOLD_ATTEMPTS = 8;

/** Fetch households, persist the first as active, expose the id.
 * Retries transient failures with backoff — the SPA can load while
 * the backend is still starting, and a mount-time failure used to
 * leave `data` null forever, silently starving every `useReady`
 * consumer (issue #430). */
export function useHouseholds(): ApiState<{ id: number; name: string }[]> {
  const [state, setState] = useState<ApiState<{ id: number; name: string }[]>>({
    data: null,
    loading: true,
    error: null,
  });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
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
        if (cancelled) return;
        if (attempt + 1 < HOUSEHOLD_ATTEMPTS) {
          setState({ data: null, loading: true, error: e.message });
          timer = setTimeout(
            () => setAttempt((a) => a + 1),
            Math.min(4000, 500 * 2 ** attempt));
        } else {
          setState({ data: null, loading: false, error: e.message });
        }
      });
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [attempt]);
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
  // Also settle on a *terminal* households failure: identity then
  // resolves (catch → []) so `useReady` lifts and each consumer's
  // own call can surface the real error — instead of every widget
  // silently rendering its empty state forever (issue #430).
  const settled = households.data !== null
    || (households.error !== null && !households.loading);
  const state = useApi(
    () =>
      api
        .get("/api/v1/profiles")
        .then((page) => {
          const items = page.items;
          const stored = getProfileId();
          const stillValid = items.some(
            (p) => p.id === stored && p.is_active);
          // Under ingress the stored id is meaningless — the Supervisor
          // supplies identity — so never write a fallback into
          // localStorage that other surfaces could mistake for "me".
          if (!stillValid && !isIngress()) {
            const fallback = items.find((p) => p.is_active && p.role === "admin")
              ?? items.find((p) => p.is_active);
            setProfileId(fallback?.id ?? null);
          }
          return items;
        })
        // Identity is optional (fresh installs have no profiles) — a
        // failed probe must never block readiness.
        .catch(() => [] as Profile[]),
    settled,
    [households.data?.length],
  );
  return state;
}

/** Resolved identity shape returned by `/api/v1/auth/me`. */
interface MeIdentity {
  id: number;
  display_name: string;
  role: string;
}

/**
 * Active member id for "Mine" scoping. Standalone reads the persisted
 * X-Profile-Id choice; under ingress it comes solely from the
 * supervisor-injected identity via `/api/v1/auth/me` — stored client
 * state is never trusted (#341).
 */
export function useMeProfileId(): number | null {
  const ingress = isIngress();
  const identity = useIdentity();
  const me = useApi(
    () => api.get("/api/v1/auth/me").then((d) => d as unknown as MeIdentity),
    ingress,
    [],
  );
  if (ingress) return me.data?.id ?? null;
  return identity.data ? getProfileId() : null;
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
