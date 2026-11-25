import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const API = process.env["FF_E2E_API"] ?? "http://localhost:8099/api/v1";

test.beforeAll(async ({ request }) => {
  const res = await request.post(`${API}/households`, {
    data: { name: "E2E Household" },
  });
  expect(res.ok()).toBe(true);
});

// axe gate on the key screens (issue #127): serious/critical
// violations fail the build; wcag2a/aa + best-practice rulesets.
for (const [name, path] of [
  ["dashboard", "/"],
  ["transactions", "/transactions"],
  ["budget", "/budget"],
] as const) {
  test(`axe: ${name} has no serious violations`, async ({ page }) => {
    await page.goto(path);
    await page.getByRole("heading", { level: 1 }).first().waitFor();
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "best-practice"])
      .analyze();
    const serious = results.violations.filter((v) =>
      ["critical", "serious"].includes(v.impact ?? ""),
    );
    expect(
      serious.map((v) => `${v.id}: ${v.nodes.length} nodes`),
    ).toEqual([]);
  });
}

test("keyboard journey: tab reaches skip-link, then content", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("heading", { level: 1 }).waitFor();
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("link", { name: "Skip to content" }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  // activating the skip link moves focus into <main>
  await expect(page.locator("#ff-main")).toBeFocused();
});

test("keyboard journey: drawer opens, Escape closes at mid width", async ({
  page,
}) => {
  await page.setViewportSize({ width: 800, height: 600 });
  await page.goto("/");
  const burger = page.getByRole("button", { name: "Open navigation" });
  await burger.focus();
  await page.keyboard.press("Enter");
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  // Radix auto-focuses the first focusable element inside the dialog —
  // the Close button; Tab then walks the nav links.
  await expect(
    drawer.getByRole("button", { name: "Close" }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(drawer).not.toBeVisible();
  await expect(burger).toBeFocused();
});

for (const opener of ["Search", "Add transaction", "More"]) {
  test(`mobile keyboard restores focus to ${opener} after Escape`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    const button = page.getByRole("button", { name: opener, exact: true });
    await button.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(button).toBeFocused();
  });
}

test("Search hands focus to Quick Add and back to its original opener", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const search = page.getByRole("button", { name: "Search", exact: true });
  await search.focus();
  await page.keyboard.press("Enter");
  const command = page.getByRole("dialog", { name: "Command menu" });
  await command.getByRole("option", { name: "Add transaction" }).click();
  const add = page.getByRole("dialog", { name: "Add transaction" });
  await expect(add.getByPlaceholder("0.00")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(search).toBeFocused();
});

test("closing keyboard search returns focus to the control used for Ctrl+K", async ({ page }) => {
  await page.goto("/");
  const opener = page.getByRole("link", { name: "Accounts", exact: true }).first();
  await opener.focus();
  await page.keyboard.press("Control+k");
  await expect(page.getByRole("dialog", { name: "Command menu" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(opener).toBeFocused();
});
