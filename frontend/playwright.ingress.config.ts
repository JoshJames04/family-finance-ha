import { defineConfig } from "@playwright/test";

/** Production bundle only; the spec owns a strict local ingress server.
 * Run `npm run build && npx playwright test -c playwright.ingress.config.ts`.
 * No backend, Docker, bank provider, or financial database is used. */
export default defineConfig({
  testDir: "./e2e",
  testMatch: "ingress-navigation.spec.ts",
  timeout: 30_000,
  workers: 1,
  use: {
    channel: process.platform === "win32" ? "msedge" : undefined,
    viewport: { width: 1280, height: 900 },
  },
});
