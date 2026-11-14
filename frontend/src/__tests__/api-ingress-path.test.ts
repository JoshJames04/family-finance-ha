import { afterEach, expect, it, vi } from "vitest";
import { api, setProfileId } from "../api/client";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  window.history.replaceState({}, "", "/");
});

// Resolve exactly as a browser does. A string-prefix assertion alone missed
// the dashboard root losing its ingress token when it has no trailing slash.
it.each([
  "/api/hassio_ingress/test-token",
  "/api/hassio_ingress/test-token/",
  "/api/hassio_ingress/test-token/budget",
  "/api/hassio_ingress/test-token/accounts/42",
  "/transactions/42",
])("keeps JSON, upload and download at the app mount from %s", async (route) => {
  window.history.replaceState({}, "", route);
  setProfileId(42);
  const mounted = route.startsWith("/api/hassio_ingress/");
  const prefix = mounted ? "/api/hassio_ingress/test-token" : "";
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const resolved = new URL(String(input), document.baseURI);
    expect(resolved.pathname).toBe(`${prefix}/api/v1/accounts`);
    expect((init?.headers as Record<string, string>)["X-Profile-Id"])
      .toBe(mounted ? undefined : "42");
    return new Response("[]", { status: 200 });
  });
  await api.get("/api/v1/accounts");
  await api.upload("/api/v1/accounts", new Blob(["test"]));
  await api.download("/api/v1/accounts");
  expect(fetcher).toHaveBeenCalledTimes(3);
});
