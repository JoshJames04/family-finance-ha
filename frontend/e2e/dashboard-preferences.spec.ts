import { expect, test, type Page } from "@playwright/test";
import { createServer, type Server } from "node:http";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

// Static production bundle plus intercepted, synthetic APIs. This fixture never
// connects to the dev backend or creates financial records.
let server: Server;
let origin: string;
let dist: string;
test.beforeAll(async () => {
  dist = await mkdtemp(resolve(tmpdir(), "ff-dashboard-preferences-"));
  await promisify(execFile)(process.execPath,
    [fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url)), "build", "--outDir", dist],
    { cwd: fileURLToPath(new URL("../", import.meta.url)) });
  server = createServer(async (req, res) => {
    const path = new URL(req.url ?? "/", "http://localhost").pathname;
    if (path.startsWith("/api/")) { res.writeHead(404).end("Unmocked synthetic API"); return; }
    const file = path.startsWith("/assets/") ? resolve(dist, `.${path}`) : resolve(dist, "index.html");
    try {
      const bytes = await readFile(file);
      res.writeHead(200, { "Content-Type": { ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2" }[extname(file)] ?? "text/html" });
      res.end(bytes);
    } catch { res.writeHead(404).end("Build the production bundle first"); }
  });
  await new Promise<void>(done => server.listen(0, "127.0.0.1", done));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Static fixture did not bind");
  origin = `http://127.0.0.1:${address.port}`;
});
test.afterAll(async () => {
  server.closeAllConnections();
  await new Promise<void>((done, fail) => server.close(error => error ? fail(error) : done()));
  if (resolve(dist).startsWith(resolve(tmpdir(), "ff-dashboard-preferences-"))) await rm(dist, { recursive: true, force: true });
});

async function fixture(page: Page, anonymous = false) {
  let saved: unknown = anonymous ? null : { order: ["upcoming", "accounts"], hidden:
    ["safe-to-spend", "budget", "kpis", "forecast", "budget-categories", "recent", "subscriptions", "system"] };
  let unavailable = false;
  let writes = 0;
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = { items: [], total: 0 };
    if (path.endsWith("/households")) body = { items: [{ id: 1, name: "Synthetic household" }], total: 1 };
    else if (path.endsWith("/profiles")) body = { items: anonymous ? [] : [{ id: 1, display_name: "Synthetic member", is_active: true, role: "member" }], total: anonymous ? 0 : 1 };
    else if (path.endsWith("/profiles/me/dashboard")) {
      if (route.request().method() === "PUT") {
        writes++;
        if (unavailable) { await route.fulfill({ status: 503, json: { title: "Synthetic save unavailable" } }); return; }
        saved = route.request().postDataJSON();
      }
      body = { role: anonymous ? null : "member", layout: saved };
    } else if (["/expected-movements", "/subscriptions", "/queue"].some(suffix => path.endsWith(suffix))) body = [];
    else if (path.endsWith("/version")) body = { version: "synthetic" };
    await route.fulfill({ json: body });
  });
  return { reject: () => { unavailable = true; }, recover: () => { unavailable = false; }, writes: () => writes };
}

for (const value of [null, {}, { order: [], hidden: null }, { widgets: ["obsolete"] }]) {
  test(`malformed browser preference ${JSON.stringify(value)} boots safely`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await fixture(page, true);
    await page.addInitScript(value => localStorage.setItem("ff.dashboard", JSON.stringify(value)), value);
    await page.goto(origin);
    await expect(page.locator("[data-widget='accounts']")).toBeVisible();
    await page.getByRole("button", { name: "Customise", exact: true }).click();
    await expect(page.getByRole("button", { name: "Hide Accounts", exact: true })).toBeVisible();
    expect(errors).toEqual([]);
  });
}

test("rejected layout remains unsaved through navigation and reload, then retry persists", async ({ page }, testInfo) => {
  const api = await fixture(page);
  await page.goto(origin);
  await expect(page.locator("[data-widget='accounts']")).toBeVisible();
  api.reject();
  await page.getByRole("button", { name: "Customise", exact: true }).click();
  await page.getByRole("button", { name: "Hide Accounts", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Changes not saved");
  await expect(page.getByRole("button", { name: "Retry saving layout" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("dashboard-unsaved.png"), fullPage: true });
  await page.getByRole("button", { name: "Close", exact: true }).click();
  for (const label of ["Transactions", "Bills", "Budget"]) {
    await page.getByRole("link", { name: label, exact: true }).first().click();
    await expect(page.getByRole("heading", { level: 1, name: label, exact: true })).toBeVisible();
  }
  await page.getByRole("link", { name: "Dashboard", exact: true }).first().click();
  await expect(page.getByRole("alert")).toContainText("Changes not saved");
  await expect(page.locator("[data-widget='accounts']")).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("Changes not saved");
  await expect(page.locator("[data-widget='accounts']")).toHaveCount(0);
  api.recover();
  await expect(page.getByRole("button", { name: "Retry saving layout" })).toBeEnabled();
  await page.getByRole("button", { name: "Retry saving layout" }).click();
  await expect(page.getByText("Layout saved to your profile.", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.locator("[data-widget='upcoming']")).toBeVisible();
  await expect(page.locator("[data-widget='accounts']")).toHaveCount(0);
  await expect(page.getByText(/Changes not saved/)).toHaveCount(0);
  expect(api.writes()).toBe(2);
});
