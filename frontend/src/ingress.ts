/** Router basename for the current page URL.
 *
 * HA ingress mounts the app under /api/hassio_ingress/<token>/ — the
 * <token> segment differs per session and cannot be baked in at build
 * time, so it is read from window.location at startup. Without this
 * the BrowserRouter basename "/" cannot match and the app renders
 * blank. Standalone deployments keep Vite's BASE_URL.
 */
export function ingressBasename(): string {
  const m = window.location.pathname.match(
    /^(\/api\/hassio_ingress\/[^/]+)/,
  );
  if (m?.[1]) return m[1];
  // Vite's `base: "./"` yields BASE_URL "./" — valid for asset URLs
  // but not a router basename; the app always mounts at root
  // standalone, so normalise it.
  const base = import.meta.env.BASE_URL ?? "/";
  return base.startsWith("/") ? base : "/";
}
