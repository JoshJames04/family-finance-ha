/** Router basename for the current page URL.
 *
 * HA ingress mounts the app under /api/hassio_ingress/<token>/ — the
 * <token> segment differs per session and cannot be baked in at build
 * time, so it is read from window.location at startup. Without this
 * the BrowserRouter basename "/" cannot match and the app renders
 * blank. Standalone deployments keep Vite's BASE_URL.
 */
const INGRESS_RE = /^\/api\/hassio_ingress\/[^/]+/;

/** True when the app is mounted inside HA ingress — identity then
 * comes from supervisor headers, never from client state. */
export function isIngress(): boolean {
  return INGRESS_RE.test(window.location.pathname);
}

export function ingressBasename(): string {
  const m = window.location.pathname.match(INGRESS_RE);
  if (m?.[0]) return m[0];
  // Vite's `base: "./"` yields BASE_URL "./" — valid for asset URLs
  // but not a router basename; the app always mounts at root
  // standalone, so normalise it.
  const base = import.meta.env.BASE_URL ?? "/";
  return base.startsWith("/") ? base : "/";
}

/** Anchor API and download URLs to the mount, independent of the route depth. */
export function appUrl(path: string): string {
  return `${ingressBasename().replace(/\/$/, "")}/${path.replace(/^\.?\//, "")}`;
}
