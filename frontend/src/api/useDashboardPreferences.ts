import { useEffect, useRef, useState } from "react";
import { ingressBasename, isIngress } from "../ingress";
import { DEFAULT_ORDER, WIDGETS } from "../widgets/registry";
import { api, getHouseholdId, getProfileId } from "./client";
import { useApi, useIdentity } from "./useApi";
import type { components } from "./schema";

type Role = components["schemas"]["Role"];
export interface DashboardLayout { order: string[]; hidden: string[] }
interface State {
  scope: string;
  layout: DashboardLayout | null;
  confirmed: DashboardLayout | null;
  role: Role | null | undefined;
  dirty: boolean;
  saving: boolean;
  error: string | null;
  saved: boolean;
}
type SaveResult = { layout: DashboardLayout; role: Role; error: string | null };
// Only active writes are retained. Route remounts share the lock; settled writes
// are removed, so an older request can never race a newer UI change.
const pending = new Map<string, Promise<SaveResult>>();
const key = (scope: string, draft = false) => `ff.dashboard.${draft ? "draft." : ""}${scope}`;

type StorageName = "localStorage" | "sessionStorage";
function read(storage: StorageName, name: string): unknown {
  try { const raw = window[storage].getItem(name); return raw ? JSON.parse(raw) : null; }
  catch { return null; }
}
function write(storage: StorageName, name: string, value: DashboardLayout | null): boolean {
  try {
    if (value) window[storage].setItem(name, JSON.stringify(value));
    else window[storage].removeItem(name);
    return true;
  } catch { return false; }
}
function normalise(value: unknown): DashboardLayout {
  const input = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const ids = new Set(WIDGETS.map(w => w.id));
  const known = (value: unknown) => Array.isArray(value)
    ? value.filter((id): id is string => typeof id === "string" && ids.has(id)) : [];
  return { order: [...new Set([...known(input.order), ...DEFAULT_ORDER])],
    hidden: [...new Set(known(input.hidden))] };
}
function local(scope: string, legacy: boolean): DashboardLayout {
  return normalise(read("localStorage", key(scope)) ?? (legacy ? read("localStorage", "ff.dashboard") : null));
}
function initial(scope: string): State {
  const draft = read("sessionStorage", key(scope, true));
  return { scope, layout: draft === null ? null : normalise(draft), confirmed: null,
    role: undefined, dirty: draft !== null, saving: pending.has(scope), error: null, saved: false };
}

export function useDashboardPreferences(ready: boolean, bootstrapError: string | null) {
  const ingress = isIngress();
  const identity = useIdentity();
  const me = useApi(() => api.get("/api/v1/auth/me"), ready && ingress);
  const profile = ingress ? me.data?.id : getProfileId();
  const scope = `${getHouseholdId() ?? "none"}:${ingress ? `ha-${profile ?? ingressBasename()}` : profile ?? "anonymous"}`;
  const remote = useApi(() => api.get("/api/v1/profiles/me/dashboard"),
    ready && identity.data !== null && (!ingress || me.data !== null), [scope]);
  const [state, setState] = useState(() => initial(scope));
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const mounted = useRef(false);
  const hydrated = useRef<string | null>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const watch = (job: Promise<SaveResult>, origin: string) => {
    void job.then(result => {
      if (!mounted.current || currentScope.current !== origin) return;
      if (!result.error) hydrated.current = origin;
      setState(s => ({ ...s, layout: result.layout, saving: false,
        role: result.role,
        saved: result.error === null,
        confirmed: result.error ? s.confirmed : result.layout,
        dirty: result.error !== null, error: result.error }));
    });
  };
  useEffect(() => {
    hydrated.current = null;
    setState(initial(scope));
    const job = pending.get(scope);
    if (job) watch(job, scope);
    // The scope is the resource identity, independent of a particular render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);
  useEffect(() => {
    if (remote.data !== null && hydrated.current !== scope) {
      hydrated.current = scope;
      const role = remote.data.role;
      const confirmed = remote.data.layout == null
        ? local(scope, !ingress && profile == null) : normalise(remote.data.layout);
      setState(s => ({ ...s, role, confirmed, layout: s.dirty ? s.layout : confirmed }));
    } else if (bootstrapError || remote.error || me.error || identity.error) {
      setState(s => s.layout !== null ? s : { ...s, layout: local(scope, !ingress && profile == null) });
    }
  }, [scope, remote.data, remote.error, me.error, identity.error, bootstrapError, ingress, profile]);

  const save = (next: DashboardLayout) => {
    if (state.scope !== scope || state.role === undefined || pending.has(scope)) return;
    const draft = normalise(next);
    const retained = write("sessionStorage", key(scope, true), draft);
    setState(s => ({ ...s, layout: draft, dirty: true, error: retained ? null
      : "This browser could not retain your draft across navigation." }));
    if (state.role === null) {
      const saved = write("localStorage", key(scope), draft);
      if (saved) write("sessionStorage", key(scope, true), null);
      setState(s => ({ ...s, confirmed: saved ? draft : s.confirmed, dirty: !saved,
        saved,
        error: saved ? null : "This browser could not save your layout." }));
      return;
    }
    setState(s => ({ ...s, saving: true }));
    const role = state.role;
    const job = api.put("/api/v1/profiles/me/dashboard", { body: draft })
      .then((): SaveResult => {
        write("localStorage", key(scope), draft);
        write("sessionStorage", key(scope, true), null);
        return { layout: draft, role, error: null };
      }).catch((error: unknown): SaveResult => ({ layout: draft,
        role,
        error: `${error instanceof Error ? error.message : "Layout save failed."}${retained ? "" : " This browser could not retain your draft across navigation."}` }))
      .finally(() => { pending.delete(scope); });
    pending.set(scope, job);
    watch(job, scope);
  };
  const revert = () => {
    if (pending.has(scope) || !state.confirmed) return;
    write("sessionStorage", key(scope, true), null);
    setState(s => ({ ...s, layout: s.confirmed, dirty: false, error: null }));
  };
  const visible = state.scope === scope ? state : initial(scope);
  return { ...visible, save, revert,
    retry: () => { if (visible.layout) save(visible.layout); },
    canRetry: visible.role !== undefined,
    canRevert: visible.confirmed !== null,
    remoteError: remote.error ?? me.error ?? identity.error,
    refetch: () => { remote.refetch(); me.refetch(); identity.refetch(); } };
}
