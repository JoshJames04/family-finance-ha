import { expect, test, type APIResponse } from "@playwright/test";

const API = process.env["FF_E2E_API"] ?? "http://localhost:8099/api/v1";
type Entry = { id: number; work_date: string; hours: string; pay_pence: number;
  status: string; revision: string; notes: string | null };
async function json<T>(pending: Promise<APIResponse>): Promise<T> {
  const response = await pending;
  expect(response.ok(), `${response.status()} ${await response.text()}`).toBe(true);
  return response.json() as Promise<T>;
}

for (const width of [1280, 390, 320]) {
  test(`weekly overtime history and mixed approvals use the real API at ${width}px`, async ({ request, page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width, height: 1000 });
    if (width === 320) await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
    const household = await json<{id: number}>(request.post(`${API}/households`, {
      data: { name: `Synthetic weekly overtime ${crypto.randomUUID()}` },
    }));
    const headers: Record<string, string> = { "X-Household-Id": String(household.id) };
    const post = <T>(path: string, data?: object) => json<T>(request.post(`${API}${path}`, { headers, data }));
    const owner = await post<{id: number}>("/profiles", { display_name: "Weekly proof owner", role: "admin" });
    headers["X-Profile-Id"] = String(owner.id);
    const employment = await post<{id: number}>("/employments", { profile_id: owner.id,
      employer: "Synthetic weekly employment", contracted_hours: "37.5", start_date: "2019-01-01" });
    await post(`/employments/${employment.id}/salary-revisions`, { effective_date: "2019-01-01", annual_salary: 3600000 });
    const path = `/employments/${employment.id}/overtime-entries`;
    const rows = [];
    for (const [i, date] of ["2025-12-29", "2025-12-30", "2025-12-31", "2026-01-01",
      "2026-01-02", "2026-01-03", "2026-01-04"].entries()) {
      rows.push(await post<Entry>(path, { work_date: date, hours: "1", notes: `Synthetic day ${i + 1}` }));
    }
    await post(`${path}/${rows[0].id}/submit`);
    await post(`${path}/${rows[0].id}/approve`);
    const adjacent = await post<Entry>(path, { work_date: "2026-01-05", hours: "2", notes: "Adjacent week untouched" });
    const old = await post<Entry>(path, { work_date: "2021-01-03", hours: "1", notes: "Old week 53 history" });
    await post(`${path}/${old.id}/submit`);
    await post(`${path}/${old.id}/approve`);
    const load = () => json<Entry[]>(request.get(`${API}${path}`, { headers }));
    const before = await load();
    await page.addInitScript(({ hid, pid }) => {
      localStorage.setItem("ff.householdId", String(hid));
      localStorage.setItem("ff.profileId", String(pid));
    }, { hid: household.id, pid: owner.id });
    await page.goto("/overtime");
    const week = () => page.getByRole("region", { name: "Week 1, 2026", exact: true });
    await expect(week()).toBeVisible();
    await expect(week()).toContainText("29 Dec 2025 – 4 Jan 2026");
    await expect(week().getByText("7h", { exact: true })).toBeVisible();
    await expect(week().getByText("Approved: 1", { exact: true })).toBeVisible();
    await expect(week().getByText("Draft: 6", { exact: true })).toBeVisible();
    await expect(page.getByRole("region", { name: "Week 53, 2020", exact: true })).toBeVisible();
    expect((await load()).map(e => [e.id, e.revision])).toEqual(before.map(e => [e.id, e.revision]));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: `e2e/artifacts/overtime-weekly-${width}.png`, fullPage: true });
    await week().getByRole("button", { name: "Submit week", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Submit 6 entries", exact: true }).click();
    await expect(week().getByText("Submitted: 6", { exact: true })).toBeVisible();
    const exception = week().getByRole("row").filter({ hasText: "Synthetic day 3" });
    await exception.getByRole("button", { name: "Reject", exact: true }).click();
    await expect(week().getByText("Rejected: 1", { exact: true })).toBeVisible();
    await week().getByRole("button", { name: "Approve week", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Approve 5 entries", exact: true }).click();
    await expect(week().getByText("Approved: 6", { exact: true })).toBeVisible();
    const added = await post<Entry>(path, { work_date: "2026-01-04", hours: "0.5", notes: "Added after submission" });
    await page.reload();
    await week().getByRole("button", { name: "Submit week", exact: true }).click();
    await json(request.patch(`${API}${path}/${added.id}`, { headers, data: { notes: "Changed during confirmation" } }));
    await page.getByRole("dialog").getByRole("button", { name: "Submit 1 entry", exact: true }).click();
    await expect(page.getByRole("dialog").getByRole("alert")).toContainText("409");
    expect((await load()).find(e => e.id === added.id)?.status).toBe("draft");
    await page.getByRole("dialog").getByRole("button", { name: "Refresh week", exact: true }).click();
    await week().getByRole("button", { name: "Submit week", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Submit 1 entry", exact: true }).click();
    await expect(week().getByText("Submitted: 1", { exact: true })).toBeVisible();
    await week().getByRole("button", { name: "Reject week", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Reject 1 entry", exact: true }).click();
    await expect(week().getByText("Rejected: 2", { exact: true })).toBeVisible();
    const after = await load();
    expect(after.find(e => e.id === adjacent.id)?.status).toBe("draft");
    expect(after.find(e => e.id === old.id)).toEqual(before.find(e => e.id === old.id));
    expect(after.filter(e => rows.some(original => original.id === e.id)).map(e => e.pay_pence))
      .toEqual(before.filter(e => rows.some(original => original.id === e.id)).map(e => e.pay_pence));
    expect(after.filter(e => e.work_date >= "2025-12-29" && e.work_date <= "2026-01-04")
      .filter(e => e.status === "approved")).toHaveLength(6);
  });
}
