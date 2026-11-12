import { expect, test } from "@playwright/test";

const API = "http://localhost:8099/api/v1";

/** #413 regression: long unbreakable bill names gave the card grid an
 * auto-sized implicit column wider than the viewport — the whole page
 * scrolled horizontally and card amounts were cropped off-frame. */
test.beforeAll(async ({ request }) => {
  const acct = await request.post(`${API}/accounts`, {
    data: {
      name: `E2E Bills Account ${crypto.randomUUID()}`,
      type: "personal",
      opening_balance: { pence: 0 },
      opening_date: "2026-01-01",
    },
  });
  expect(acct.ok()).toBe(true);
  const { id } = (await acct.json()) as { id: number };

  const bill = await request.post(`${API}/commitments`, {
    data: {
      name: "Supercalifragilisticexpialidocious-Insurance-Premium",
      type: "bill",
      amount: { pence: 123456789 },
      frequency: "annually",
      day_of_month: 1,
      next_date: "2027-01-01",
      account_id: id,
    },
  });
  expect(bill.ok()).toBe(true);
});

for (const width of [320, 375, 430]) {
  test(`bills page has no horizontal overflow at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/bills");
    await expect(
      page.getByRole("heading", { name: "Bills" }),
    ).toBeVisible();
    // Wait for a bill card to render before measuring.
    await expect(
      page.getByText("Supercalifragilisticexpialidocious", { exact: false })
        .first(),
    ).toBeVisible();

    const { scrollWidth, clientWidth } = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth);

    // Every rendered money amount sits fully inside the viewport —
    // cropped card amounts were the user-visible symptom.
    const clipped = await page.evaluate(() => {
      const cw = document.documentElement.clientWidth;
      return [...document.querySelectorAll(".ff-money")]
        .filter((el) => el.getBoundingClientRect().right > cw + 1)
        .map((el) => el.textContent);
    });
    expect(clipped).toEqual([]);
  });
}

