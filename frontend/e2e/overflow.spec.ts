import { expect, test } from "@playwright/test";

/**
 * #413 system-wide guard: every top-level route must fit the viewport
 * horizontally at mobile widths. Empty states still exercise the page
 * chrome (nav, headers, card shells); bills.spec.ts adds seeded-content
 * coverage for the densest grid.
 */
const ROUTES = [
  "/",
  "/accounts",
  "/banking",
  "/bills",
  "/budget",
  "/catalogue",
  "/energy",
  "/forecast",
  "/gifts",
  "/goals",
  "/loans",
  "/notifications",
  "/overtime",
  "/payees",
  "/reports",
  "/review",
  "/rules",
  "/salary",
  "/scenarios",
  "/settings",
  "/shared",
  "/subscriptions",
  "/transactions",
  "/upcoming",
];

for (const route of ROUTES) {
  test(`${route} has no horizontal overflow at 320px`, async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 800 });
    await page.goto(route);
    // Page shell rendered before measuring.
    await expect(page.locator("main, [role=main], body > div").first())
      .toBeVisible();

    const { scrollWidth, clientWidth } = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth);

    // No money amount may sit partly off the right edge.
    const clipped = await page.evaluate(() => {
      const cw = document.documentElement.clientWidth;
      return [...document.querySelectorAll(".ff-money")]
        .filter((el) => el.getBoundingClientRect().right > cw + 1)
        .map((el) => el.textContent);
    });
    expect(clipped).toEqual([]);
  });
}
