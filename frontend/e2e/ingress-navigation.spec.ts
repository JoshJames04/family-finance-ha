import { expect, test as base } from "@playwright/test";
import { execFile } from "node:child_process";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const prefix = "/api/hassio_ingress/synthetic-ingress-token";
const dist = fileURLToPath(new URL("../dist/", import.meta.url));
const hidden = ["safe-to-spend", "budget", "kpis", "forecast", "budget-categories",
  "recent", "subscriptions", "system"];

/** Synthetic read-only API responses isolate browser URL resolution from
 * authentication, provider availability, and real household data. Assets and
 * navigation use the actual built production bundle without HTML rewriting. */
function responseFor(path: string): unknown {
  if (path === "/api/v1/households") return { items: [{ id: 1, name: "Synthetic household" }] };
  if (path === "/api/v1/profiles") return { items: [{ id: 1, display_name: "Synthetic member", role: "admin", is_active: true }] };
  if (path === "/api/v1/auth/me") return { id: 1, display_name: "Synthetic member", role: "admin" };
  if (path === "/api/v1/profiles/me/dashboard") return { role: "admin", layout: { order: ["upcoming"], hidden } };
  if (["/api/v1/accounts", "/api/v1/categories", "/api/v1/budgets", "/api/v1/transactions"].includes(path)) return { items: [] };
  if (path === "/api/v1/version") return { version: "synthetic" };
  return [];
}

type Ingress = { origin: string; api: string[]; denied: string[] };
const test = base.extend<{ ingress: Ingress; ingressMount: string }>({
  ingressMount: [prefix, { option: true }],
  ingress: async ({ ingressMount }, use) => {
    const api: string[] = [];
    const denied: string[] = [];
    const server = createServer(async (req, res) => {
      const path = new URL(req.url ?? "/", "http://localhost").pathname;
      // A missing/replaced session token must fail, rather than being hidden
      // by an accommodating SPA fallback or a broad Playwright API mock.
      if (ingressMount && path !== ingressMount && !path.startsWith(`${ingressMount}/`)) {
        if (path.includes("/api/v1/") || path.includes("/assets/")) denied.push(path);
        res.writeHead(404).end("Ingress token missing");
        return;
      }
      const mounted = path.slice(ingressMount.length);
      if (mounted.startsWith("/api/v1/")) {
        api.push(path);
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify(responseFor(mounted)));
        return;
      }
      const asset = mounted.startsWith("/assets/");
      const file = asset ? resolve(dist, `.${mounted}`) : resolve(dist, "index.html");
      try {
        const content = await readFile(file);
        const type = { ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2" }[extname(file)] ?? "text/html";
        res.writeHead(200, { "Content-Type": type });
        res.end(content);
      } catch {
        denied.push(path);
        res.writeHead(404).end("Production asset missing; run npm run build");
      }
    });
    await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    if (address === null || typeof address === "string") throw new Error("Local ingress server did not bind");
    try {
      await use({ origin: `http://127.0.0.1:${address.port}`, api, denied });
    } finally {
      server.closeAllConnections();
      await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    }
  },
});

test.beforeAll(async () => {
  // The normal CI e2e job runs the dev stack, so create a fresh production
  // bundle here too. This also prevents a stale dist hiding source changes.
  await promisify(execFile)(process.execPath,
    [fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url)), "build"],
    { cwd: fileURLToPath(new URL("../", import.meta.url)) });
});

test("Budget to Dashboard keeps the session token on every API request", async ({ page, ingress }) => {
  await page.goto(`${ingress.origin}${prefix}/budget`);
  await expect(page.getByRole("heading", { level: 1, name: "Budget", exact: true })).toBeVisible();
  const beforeDashboard = ingress.api.length;
  await page.getByRole("link", { name: "Dashboard", exact: true }).first().click();
  await expect(page).toHaveURL(`${ingress.origin}${prefix}/`);
  await expect(page.locator("[data-widget='upcoming']")).toContainText("Nothing scheduled");
  await expect.poll(() => ingress.api.slice(beforeDashboard)
    .some(path => path.endsWith("/api/v1/accounts"))).toBe(true);
  expect(ingress.api.some(path => path.endsWith("/expected-movements"))).toBe(true);
  expect(ingress.api.every(path => path.startsWith(`${prefix}/api/v1/`))).toBe(true);
  expect(ingress.denied).toEqual([]);
});

test("generated Dashboard root reload retains the ingress token on production assets", async ({ page, ingress }) => {
  await page.goto(`${ingress.origin}${prefix}/budget`);
  await page.getByRole("link", { name: "Dashboard", exact: true }).first().click();
  await expect(page).toHaveURL(`${ingress.origin}${prefix}/`);
  await expect(page.locator("[data-widget='upcoming']")).toContainText("Nothing scheduled");
  const requestsBeforeReload = ingress.api.length;
  await page.reload();
  await expect(page).toHaveURL(`${ingress.origin}${prefix}/`);
  await expect(page.locator("[data-widget='upcoming']"),
    "The production HTML must boot after reloading the generated Dashboard root").toBeVisible();
  expect(ingress.api.length).toBeGreaterThan(requestsBeforeReload);
  expect(ingress.denied, "Reloaded production assets must retain the ingress token").toEqual([]);
});

test("slashless ingress entry preserves search and fragment when canonicalized", async ({ page, ingress }) => {
  await page.goto(`${ingress.origin}${prefix}?synthetic=1#ff-main`);
  await expect(page).toHaveURL(`${ingress.origin}${prefix}/?synthetic=1#ff-main`);
  await expect(page.locator("[data-widget='upcoming']")).toContainText("Nothing scheduled");
  expect(ingress.api.every(path => path.startsWith(`${prefix}/api/v1/`))).toBe(true);
  // A legacy slashless document can speculatively preload relative assets
  // before its inline URL guard executes. It still boots; generated links
  // use token/ and the navigation/reload test permits no such rejected calls.
  expect(ingress.denied.every(path => path.startsWith("/api/hassio_ingress/assets/"))).toBe(true);
});

test.describe("standalone production bundle", () => {
  test.use({ ingressMount: "" });
  test("root still boots without an ingress mount", async ({ page, ingress }) => {
    await page.goto(`${ingress.origin}/`);
    await expect(page).toHaveURL(`${ingress.origin}/`);
    await expect(page.locator("[data-widget='upcoming']")).toContainText("Nothing scheduled");
    expect(ingress.api.some(path => path.endsWith("/expected-movements"))).toBe(true);
    expect(ingress.api.every(path => path.startsWith("/api/v1/"))).toBe(true);
    expect(ingress.denied).toEqual([]);
  });
});
