/**
 * Visual audit: screenshots of every route at desktop (dark + light),
 * tablet and mobile widths against a running stack. Output goes to the
 * gitignored .ux-audit/ directory.
 *
 *   node scripts/screenshots.mjs [label] [baseUrl] [routes] [modes]
 *
 *   routes  comma list, e.g. "dashboard,budget" (default: all)
 *   modes   comma list of dark,light,tablet,mobile (default: all)
 */
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

const label = process.argv[2] ?? "shot";
const base = process.argv[3] ?? "http://localhost:5173";
const ALL = ["dashboard", "transactions", "budget", "forecast", "review",
  "upcoming", "bills", "subscriptions", "goals", "gifts", "loans", "accounts",
  "salary", "energy", "reports", "settings", "catalogue"];
const routes = process.argv[4] ? process.argv[4].split(",") : ALL;
const MODES = {
  dark: { width: 1440, height: 900, scheme: "dark", full: true },
  light: { width: 1440, height: 900, scheme: "light", full: true },
  tablet: { width: 820, height: 1180, scheme: "light", full: false },
  mobile: { width: 390, height: 844, scheme: "dark", full: false },
};
const modes = process.argv[5] ? process.argv[5].split(",") : Object.keys(MODES);
const dir = fileURLToPath(new URL("../../.ux-audit/", import.meta.url));
mkdirSync(dir, { recursive: true });

const browser = await chromium.launch();
const errors = [];
for (const mode of modes) {
  const m = MODES[mode];
  const page = await browser.newPage({
    viewport: { width: m.width, height: m.height },
    colorScheme: m.scheme,
  });
  page.on("pageerror", (e) => errors.push(`${mode} ${page.url()}: ${e.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`${mode} ${page.url()}: ${msg.text()}`);
  });
  for (const r of routes) {
    const path = r === "dashboard" ? "" : r;
    await page.goto(`${base}/${path}`, { waitUntil: "networkidle" }).catch(() => {});
    await page.waitForTimeout(700);
    await page.screenshot({ path: `${dir}${label}-${mode}-${r}.png`, fullPage: m.full });
  }
  await page.close();
}
await browser.close();
if (errors.length) console.log(`console errors:\n${[...new Set(errors)].join("\n")}`);
console.log(`screenshots written to .ux-audit/ (${label})`);
