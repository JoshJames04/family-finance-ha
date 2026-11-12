/**
 * Data hooks for the shell pages: `useHouseholds` resolves the active
 * household (first one, persisted), `useApi` runs a typed request and
 * exposes loading/error/data states.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { isIngress } from "../ingress";
import { api, ApiError, getHouseholdId, getProfileId, setHouseholdId,
  setProfileId } from "./client";
import type { components } from "./schema";

export type Profile = components["schemas"]["ProfileOut"];

/** The five states every consumer must distinguish (issue #460):
 * loading (nothing asked or first flight) / data / honest-empty /
 * retrying (fetch in flight, stale data still shown) / failed. An
 * error must never masquerade as an empty dataset — `data === null`
 * with `loading === false && error === null` means "asked, got
 * nothing", never "haven't asked yet". */
export interface ApiState<T> {
  data: T | null;
  loading: boolean;
  /** A retry/backoff pass is running while `data` may hold the last
   * good response — render stale data rather than a skeleton. */
  retrying: boolean;
  error: string | null;
  /** Restart the attempt sequence — the Retry action's handler. */
  refetch: () => void;
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
  const [state, setState] = useState<
    Omit<ApiState<{ id: number; name: string }[]>, "refetch">>({
    data: null,
    loading: true,
    retrying: false,
    error: null,
  });
  const [attempt, setAttempt] = useState(0);
  const [tick, setTick] = useState(0);
  const refetch = useCallback(() => {
    setAttempt(0);
    setTick((t) => t + 1);
  }, []);
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
        setState({ data: items, loading: false, retrying: false,
                   error: null });
      })
      .catch((e: Error) => {
        if (cancelled) return;
        if (attempt + 1 < HOUSEHOLD_ATTEMPTS) {
          setState({ data: null, loading: true, retrying: true,
                     error: e.message });
          timer = setTimeout(
            () => setAttempt((a) => a + 1),
            Math.min(4000, 500 * 2 ** attempt));
        } else {
          setState({ data: null, loading: false, retrying: false,
                     error: e.message });
        }
      });
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [attempt, tick]);
  return { ...state, refetch };
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

/** Total attempts per fetch cycle (initial + retries). Transient
 * failures — network drops and 429/5xx from an upstream that is
 * briefly busy (ingress restart, a sync holding the DB writer) —
 * retry with exponential backoff: ~500ms, 1s, 2s, 4s → ~7.5s of
 * cover, bounded so a dead backend surfaces an error promptly.
 * 4xx (auth, validation, not-found) never retries. */
const MAX_ATTEMPTS = 4;
const TRANSIENT_STATUSES = new Set([429, 500, 502, 503, 504]);

function isTransient(e: unknown): boolean {
  // ApiError carries an HTTP status; anything else (TypeError from
  // fetch, aborted connections) is a transport blip → transient.
  if (e instanceof ApiError) return TRANSIENT_STATUSES.has(e.status);
  return true;
}

/**
 * Run a typed API call once `ready` is true (typically once the
 * household id is known). Re-runs when `ready` flips or `deps` change.
 *
 * #460 semantics: `loading` is true until the first settled response
 * (including while `ready` is still false — a gated request is
 * pending, not "loaded and empty"). Transient failures retry in the
 * background; once `data` exists it is kept (stale) while a refresh
 * fails, so a blip never re-renders real records as £0/empty.
 */
export function useApi<T>(
  fetcher: () => Promise<T>,
  ready: boolean,
  deps: readonly unknown[] = [],
): ApiState<T> {
  const [state, setState] = useState<
    Omit<ApiState<T>, "refetch">>({
    data: null,
    loading: true,
    retrying: false,
    error: null,
  });
  const fnRef = useRef(fetcher);
  fnRef.current = fetcher;
  // Generation counter: only the newest fetch cycle may write
  // state — a late response from a stale cycle (deps changed,
  // refetch fired) must never clobber fresher data.
  const genRef = useRef(0);
  const [tick, setTick] = useState(0);
  const refetch = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    if (!ready) {
      // Gated: nothing asked yet — report pending, never empty.
      setState((s) => (s.loading && s.data === null
        ? s
        : { data: s.data, loading: true, retrying: false,
            error: s.error }));
      return;
    }
    const gen = ++genRef.current;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = (attempt: number) => {
      setState((s) => ({
        data: s.data,
        loading: s.data === null,   // skeleton only with nothing to show
        retrying: attempt > 0 || s.data !== null,
        error: null,
      }));
      fnRef
        .current()
        .then((data) => {
          if (cancelled || gen !== genRef.current) return;
          setState({ data, loading: false, retrying: false,
                     error: null });
        })
        .catch((e: Error) => {
          if (cancelled || gen !== genRef.current) return;
          if (isTransient(e) && attempt + 1 < MAX_ATTEMPTS) {
            // Stay in the loading/retrying band — the failure is
            // being absorbed; it is NOT a terminal error state yet.
            setState((s) => ({ data: s.data,
                              loading: s.data === null,
                              retrying: true, error: null }));
            timer = setTimeout(
              () => run(attempt + 1),
              Math.min(4000, 500 * 2 ** attempt));
          } else {
            // Terminal: keep the last good data (stale-but-real)
            // alongside the error so consumers can show both.
            setState((s) => ({ data: s.data, loading: false,
                              retrying: false, error: e.message }));
          }
        });
    };
    run(0);
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, tick, ...deps]);
  return { ...state, refetch };
}
