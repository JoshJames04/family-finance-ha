/**
 * Theme preference: "system" (default — follows HA's explicit theme
 * when accessible, otherwise prefers-color-scheme), or a
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
  try {
    // Ingress is embedded in the same-origin HA frontend. Cross-origin
    // standalone embeds cannot inspect their parent and use the OS.
    const ha = window.parent.document.querySelector("home-assistant") as
      (Element & { hass?: { themes?: { darkMode?: boolean } } }) | null;
    const dark = ha?.hass?.themes?.darkMode;
    if (typeof dark === "boolean") return dark ? "dark" : "light";
  } catch { /* parent is not accessible */ }
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
  // HA's reactive property changes do not emit OS media-query events.
  const timer = window.setInterval(cb, 2_000);
  return () => {
    window.clearInterval(timer);
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
