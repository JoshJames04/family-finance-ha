/**
 * Theme preference: "system" (default — follows prefers-color-scheme,
 * which HA ingress also follows when its theme is "auto"), or a
 * manual "light"/"dark" override persisted in localStorage. The
 * resolved scheme lands on <html data-theme> for the token layer.
 */
import { useEffect, useSyncExternalStore } from "react";

export type Scheme = "light" | "dark";
export type ThemePref = "system" | Scheme;

const KEY = "ff.theme";
const listeners = new Set<() => void>();

const media = (() => {
  try {
    return typeof window !== "undefined" && "matchMedia" in window
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;
  } catch {
    return null; // jsdom stubs matchMedia but throws
  }
})();

function readPref(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

function resolve(pref: ThemePref): Scheme {
  if (pref !== "system") return pref;
  return media?.matches ? "dark" : "light";
}

function snapshot(): string {
  const pref = readPref();
  return `${pref}:${resolve(pref)}`;
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  const onMedia = () => cb();
  media?.addEventListener?.("change", onMedia);
  return () => {
    listeners.delete(cb);
    media?.removeEventListener?.("change", onMedia);
  };
}

export function setThemePref(pref: ThemePref): void {
  try {
    if (pref === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, pref);
  } catch { /* storage unavailable — session-only */ }
  for (const l of listeners) l();
}

export function useTheme(): { pref: ThemePref; scheme: Scheme } {
  const snap = useSyncExternalStore(subscribe, snapshot, snapshot);
  const [pref, scheme] = snap.split(":") as [ThemePref, Scheme];
  useEffect(() => {
    document.documentElement.dataset.theme = scheme;
  }, [scheme]);
  return { pref, scheme };
}
