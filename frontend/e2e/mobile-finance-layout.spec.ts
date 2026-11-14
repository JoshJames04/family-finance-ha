import { expect, test, type Page } from "@playwright/test";

const widths = [320, 360, 375, 390, 414, 430, 768, 1024];
test.describe.configure({ mode: "parallel" });
const member = `Member${"A".repeat(84)}`;
const other = `Member${"B".repeat(84)}`;
const potName = `SharedAccount${"C".repeat(84)}`;
const category = `HouseholdCategory${"D".repeat(75)}`;
type State = "populated" | "empty" | "loading" | "error" | "pot-empty" | "pot-loading" | "pot-error";

async function fixture(page: Page, state: State) {
  const now = new Date();
  const start = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-01`;
  const end = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate()}`;
  const profiles = [{ id: 1, household_id: 1, display_name: member, role: "admin", is_active: true },
    { id: 2, household_id: 1, display_name: other, role: "member", is_active: true }];
  const accounts = [
    { id: 10, owner_profile_id: null, name: potName }, { id: 11, owner_profile_id: null, name: `${potName}Second` },
    { id: 21, owner_profile_id: 1, name: `${member}Account` }, { id: 22, owner_profile_id: 2, name: `${other}Account` },
  ].map(a => ({ ...a, household_id: 1, type: "personal", archived: false, opening_balance_pence: 0, opening_date: start }));
  const rules = [{ id: 1, source_account_id: 21, dest_account_id: 10, pence: 1234567890 },
    { id: 2, source_account_id: 22, dest_account_id: 10, pence: 987654321 },
    { id: 3, source_account_id: 21, dest_account_id: 11, pence: 100000 }].map(r => ({
      ...r, household_id: 1, name: `StandingPayment${"E".repeat(80)}${r.id}`, active: true,
      amount_spec: { kind: "fixed", pence: r.pence }, frequency: "monthly", day_of_month: 1,
    }));
  const movements = rules.map(r => ({ id: 100 + r.id, household_id: 1, account_id: r.dest_account_id,
    amount_pence: r.pence, outstanding_pence: r.pence - 100000, dismissed_at: null,
    expected_date: start, label: r.name, confidence: "scheduled", source_type: "transfer_rule", source_id: r.id }));
  const budget = { id: 1, owner_profile_id: null, name: "Shared plan", period_type: "monthly", start_date: start, end_date: end, status: "approved" };
  const detail = { ...budget, read_only: false, lines: [{ id: 11, category_id: 1, side: "expense", original_pence: 1234567890,
    revised_pence: null, effective_pence: 1234567890, rollover: false }] };
  const actuals = { budget_id: 1, spent_pence: 111111111, income_received_pence: 222222222,
    as_of: start, budget_covered_pence: 111111111, unallocated_pence: 0, uncategorised_pence: 0,
    bills_remaining_pence: 1234567890, bills_remaining_count: 2, spend_items: [],
    lines: { "11": { original_pence: 1234567890, revised_pence: null, revision_reason: null,
      effective_pence: 1234567890, actual_pence: 111111111, variance_pence: 1123456779 } } };
  const recorded: number[] = [];
  await page.route("**/api/v1/**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    let body: unknown = { items: [], total: 0 };
    if (path.endsWith("/budgets") && state === "loading") return;
    if (path.endsWith("/budgets") && state === "error") {
      await route.fulfill({ status: 400, json: { detail: "Synthetic budget unavailable" } }); return;
    }
    if (path.endsWith("/transfer-rules") && state === "pot-loading") return;
    if (path.endsWith("/transfer-rules") && state === "pot-error") {
      await route.fulfill({ status: 400, json: { detail: "Synthetic shares unavailable" } }); return;
    }
    if (path.endsWith("/households")) body = { items: [{ id: 1, name: "Synthetic mobile household" }], total: 1 };
    else if (path.endsWith("/profiles")) body = { items: profiles, total: 2 };
    else if (path.endsWith("/accounts")) body = { items: accounts, total: 4 };
    else if (path.endsWith("/budgets")) body = { items: state === "empty" ? [] : [budget,
      { ...budget, id: 2, owner_profile_id: 1 }, { ...budget, id: 3, owner_profile_id: 2 }], total: state === "empty" ? 0 : 3 };
    else if (path.endsWith("/reports/combined")) body = { detail: { ...detail, id: 0, read_only: true }, actuals };
    else if (path.endsWith("/actuals")) body = actuals;
    else if (path.endsWith("/budgets/1")) body = detail;
    else if (path.endsWith("/categories")) body = { items: [{ id: 1, name: category, parent_id: null, household_id: 1 }], total: 1 };
    else if (path.endsWith("/transfer-rules")) body = { items: state === "pot-empty" ? [] : rules, total: state === "pot-empty" ? 0 : 3 };
    else if (path.endsWith("/contributions/by-member")) body = profiles.map(p => ({ profile_id: p.id, contributed_pence: 100000 }));
    else if (path.endsWith("/contributions")) body = movements.map(m => ({ movement_id: m.id, matched_pence: 100000, outstanding_pence: m.outstanding_pence }));
    else if (path.endsWith("/expected-movements")) body = movements;
    else if (path.endsWith("/transactions")) {
      if (url.searchParams.get("direction") !== "income") {
        await route.fulfill({ status: 422, json: { detail: "direction must be income or expense" } }); return;
      }
      body = { items: [{ id: 900, account_id: 10, date: start, payee: `Incoming${"F".repeat(90)}`, amount_pence: 100000 }], total: 1 };
    }
    else if (path.endsWith("/matches") && request.method() === "POST") {
      recorded.push(request.postDataJSON().transaction_id); body = { id: 901 };
    }
    else if (path.endsWith("/match")) body = { matched: 0 };
    else if (path.endsWith("/version")) body = { version: "synthetic" };
    else if (path.endsWith("/queue")) body = [];
    await route.fulfill({ json: body });
  });
  return recorded;
}

async function overflow(page: Page) {
  return page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const detailScroll = (el: Element) => {
      for (let p: Element | null = el; p; p = p.parentElement) {
        if (["auto", "scroll"].includes(getComputedStyle(p).overflowX) && p.querySelector("table")) return true;
      }
      return false;
    };
    const bad = [...document.querySelectorAll("main *, [role=dialog], [role=dialog] *")].filter(el => {
      const box = el.getBoundingClientRect();
      const scrolls = ["auto", "scroll"].includes(getComputedStyle(el).overflowX)
        && el.scrollWidth > el.clientWidth + 1;
      return box.width > 0 && box.height > 0 && (box.right > width + 1 || box.left < -1 || scrolls) && !detailScroll(el);
    }).map(el => ({ tag: el.tagName, text: el.textContent?.slice(0, 70), class: el.className,
      right: Math.round(el.getBoundingClientRect().right), width: Math.round(el.getBoundingClientRect().width) }));
    const clippedMoney = [...document.querySelectorAll("main .ff-money, [role=dialog] .ff-money")].filter(el => {
      if (detailScroll(el)) return false;
      const box = el.getBoundingClientRect();
      for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
        const parent = p.getBoundingClientRect();
        if (["hidden", "clip"].includes(getComputedStyle(p).overflowX) && (box.right > parent.right + 1 || box.left < parent.left - 1)) return true;
      }
      return false;
    }).map(el => el.textContent);
    return { viewport: width, page: document.documentElement.scrollWidth, bad: bad.slice(0, 16), clippedMoney };
  });
}

async function touchActions(page: Page, width: number) {
  for (const name of ["Match payments", "Adjust split", "Set a share"]) {
    const control = page.getByRole("button", { name, exact: true });
    await control.evaluate(el => el.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" }));
    const box = (await control.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(width + 1);
    expect(box.height).toBeGreaterThanOrEqual(44);
    const hit = await control.evaluate(el => {
      const b = el.getBoundingClientRect();
      const target = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
      return { hit: target?.closest("button") === el, target: target?.outerHTML.slice(0, 200), y: b.y };
    });
    expect(hit.hit, JSON.stringify(hit)).toBe(true);
  }
  const selector = page.getByRole("combobox", { name: "Shared pot", exact: true });
  await expect(selector).toBeVisible();
  await selector.evaluate(el => el.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" }));
  const box = (await selector.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(width + 1);
  expect(box.height).toBeGreaterThanOrEqual(44);
  expect(await selector.evaluate(el => {
    const b = el.getBoundingClientRect();
    return document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2) === el;
  })).toBe(true);
}

for (const width of widths) for (const state of ["populated", "empty", "loading", "error"] as const) for (const path of ["/shared", "/budget"]) {
  test(`${state} ${path.slice(1)} layout at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const recorded = await fixture(page, state);
      await page.goto(path);
      if (state === "populated") {
        await expect(page.getByRole("button", { name: /^Record .*'s payment$/ }).first()).toBeVisible();
        await page.getByRole("button", { name: /^Share rules/ }).click();
        await page.evaluate(() => document.fonts.ready);
      }
      else if (state === "error") await expect(page.getByRole("alert").first()).toBeVisible();
      else if (state === "loading") await expect(page.getByRole("status", { name: "Loading" }).first()).toBeVisible();
      else await expect(page.getByText(path === "/shared" ? "No shared budget" : "No budgets created", { exact: true })).toBeVisible();
      const result = await overflow(page);
      expect(result, JSON.stringify(result, null, 2)).toEqual({ viewport: width, page: width, bad: [], clippedMoney: [] });
      if (state === "populated") {
        await touchActions(page, width);
        await expect(page.getByRole("heading", { name: `Shared pot — ${potName}`, exact: true })).toBeVisible();
        await page.getByRole("button", { name: "Adjust split", exact: true }).click();
        await expect(page.getByRole("dialog")).toBeVisible();
        let dialogLayout = await overflow(page);
        expect(dialogLayout, JSON.stringify(dialogLayout)).toEqual({ viewport: width, page: width, bad: [], clippedMoney: [] });
        await page.keyboard.press("Escape");
        await page.getByRole("button", { name: "Set a share", exact: true }).click();
        await expect(page.getByRole("dialog")).toBeVisible();
        dialogLayout = await overflow(page);
        expect(dialogLayout, JSON.stringify(dialogLayout)).toEqual({ viewport: width, page: width, bad: [], clippedMoney: [] });
        await page.keyboard.press("Escape");
        await page.getByRole("button", { name: /^Record .*'s payment$/ }).first().click();
        const dialog = page.getByRole("dialog");
        await expect(dialog).toBeVisible();
        dialogLayout = await overflow(page);
        expect(dialogLayout, JSON.stringify(dialogLayout)).toEqual({ viewport: width, page: width, bad: [], clippedMoney: [] });
        await expect(dialog.getByRole("combobox", { name: "Transfer transaction" })).toContainText("Incoming");
        await dialog.getByRole("combobox", { name: "Transfer transaction" }).selectOption("900");
        await dialog.getByRole("button", { name: "Record payment", exact: true }).click();
        await expect(dialog).toHaveCount(0);
        expect(recorded).toContain(900);
        if ([320, 390, 768, 1024].includes(width)) {
          await page.getByRole("button", { name: /^Share rules/ }).click();
          await expect(page.locator("[data-sonner-toast]")).toHaveCount(0);
          await page.evaluate(() => window.scrollTo(0, 0));
          // Full-page documentation capture: keep fixed navigation at the
          // image edge, after assertions at the real device viewport above.
          const captureHeight = await page.evaluate(() => document.documentElement.scrollHeight);
          await page.setViewportSize({ width, height: captureHeight });
          await page.screenshot({ path: `e2e/artifacts/mobile-finance-${path.slice(1)}-${width}.png`, fullPage: true, animations: "disabled" });
        }
      }
  });
}

test("Record payment loads valid incoming transactions", async ({ page }) => {
  await fixture(page, "populated");
  await page.goto("/shared");
  await expect(page.getByRole("button", { name: /^Record .*'s payment$/ }).first()).toBeVisible();
  await page.getByRole("button", { name: /^Record .*'s payment$/ }).first().click();
  await expect(page.getByRole("dialog").getByRole("combobox", { name: "Transfer transaction" })).toContainText("Incoming");
});

for (const state of ["pot-empty", "pot-loading", "pot-error"] as const) for (const path of ["/shared", "/budget"]) {
  test(`${state} panel keeps mobile actions available on ${path}`, async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 900 });
    await fixture(page, state);
    await page.goto(path);
    if (state === "pot-loading") await expect(page.getByRole("status", { name: "Loading" }).last()).toBeVisible();
    else if (state === "pot-error") await expect(page.getByRole("alert").filter({ hasText: "Synthetic shares unavailable" })).toBeVisible();
    else await expect(page.getByText("No shares set", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Set a share", exact: true })).toBeVisible();
    const result = await overflow(page);
    expect(result, JSON.stringify(result)).toEqual({ viewport: 320, page: 320, bad: [], clippedMoney: [] });
  });
}

test("desktop Shared actions retain keyboard activation and focus return", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await fixture(page, "populated");
  await page.goto("/shared");
  await expect(page.getByRole("button", { name: /^Record .*'s payment$/ }).first()).toBeVisible();
  const match = page.getByRole("button", { name: "Match payments", exact: true });
  await match.focus();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Adjust split", exact: true })).toBeFocused();
  await page.keyboard.press("Tab");
  const share = page.getByRole("button", { name: "Set a share", exact: true });
  await expect(share).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(share).toBeFocused();
});

for (const smoke of [{ name: "dark reduced motion", width: 375, height: 900, dark: true },
  { name: "landscape", width: 768, height: 375, dark: false }]) {
  test(`populated ${smoke.name} finance remains usable`, async ({ page }) => {
    await page.setViewportSize({ width: smoke.width, height: smoke.height });
    if (smoke.dark) await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
    await fixture(page, "populated");
    for (const path of ["/shared", "/budget"]) {
      await page.goto(path);
      await expect(page.getByRole("button", { name: /^Record .*'s payment$/ }).first()).toBeVisible();
      if (smoke.dark) {
        await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
        expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
      }
      await page.evaluate(() => document.fonts.ready);
      const result = await overflow(page);
      expect(result, JSON.stringify(result)).toEqual({ viewport: smoke.width, page: smoke.width, bad: [], clippedMoney: [] });
      await touchActions(page, smoke.width);
      await page.getByRole("button", { name: "Match payments", exact: true }).click();
      await expect(page.getByText("No new matches found", { exact: true })).toBeVisible();
      await page.getByRole("button", { name: "Adjust split", exact: true }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();
      const box = (await dialog.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.y).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(smoke.width + 1);
      expect(box.y + box.height).toBeLessThanOrEqual(smoke.height + 1);
      await expect(dialog.getByRole("button", { name: "Save split", exact: true })).toBeVisible();
      await page.keyboard.press("Escape");
    }
  });
}
