/**
 * Light/dark scheme detection. Home Assistant ingress does not hand
 * its theme object to framed add-ons, so the reliable signal is
 * `prefers-color-scheme` (which HA follows when the user's theme is
 * set to "auto"); any HA CSS vars actually present in the document
 * still take precedence via the fallback chains in theme.css.
 */
import { useEffect, useState } from "react";

export type Scheme = "light" | "dark";

const media = (() => {
  try {
    return typeof window !== "undefined" && "matchMedia" in window
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;
  } catch {
    return null; // jsdom stubs matchMedia but throws
  }
})();

function current(): Scheme {
  return media?.matches ? "dark" : "light";
}

export function useTheme(): Scheme {
  const [scheme, setScheme] = useState<Scheme>(current);
  useEffect(() => {
    if (!media) return;
    const onChange = (e: MediaQueryListEvent) =>
      setScheme(e.matches ? "dark" : "light");
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = scheme;
  }, [scheme]);
  return scheme;
}
