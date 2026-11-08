/** #296: salary is an effective-dated series — the card shows the
 * dated history, "New salary" records a revision from a date, and
 * adding an employment can seed its first revision. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Salary } from "../pages/Salary";

const EMP = {
  id: 1, profile_id: 1, employer: "Acme Ltd", jurisdiction: "england_wales",
  ni_category: "A", student_loan_plan: "none", pension_mode: "none",
  pension_employee_bps: 0, pension_fixed_pence: 0,
  contracted_hours: "37.5", work_days_per_week: "5",
  payday_rule: "last_working_day", active: true,
};

const PROFILES = [
  { id: 1, display_name: "Alice", role: "admin", is_active: true,
    totp_enabled: false },
];

const REVISIONS = [
  { id: 2, employment_id: 1, effective_date: "2026-04-06",
    annual_salary_pence: 4500000, notes: "April pay rise" },
  { id: 1, employment_id: 1, effective_date: "2025-09-01",
    annual_salary_pence: 4200000, notes: null },
];

function stubFetch(calls: { url: string; method: string; body: string }[],
  opts: { employments?: object[] } = {}) {
  const employments = opts.employments ?? [EMP];
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: String(init?.body ?? "") });
      let body: object | object[] = { items: [], total: 0 };
      if (method === "POST" && url.endsWith("/employments")) {
        body = { ...EMP, id: 7 };
      } else if (method !== "GET") {
        body = { ok: true };
      } else if (url.includes("/api/v1/households")) {
        body = { items: [{ id: 1, name: "Home" }], total: 1 };
      } else if (url.includes("/api/v1/profiles")) {
        body = { items: PROFILES, total: PROFILES.length };
      } else if (url.includes("/salary-revisions")) {
        body = REVISIONS;
      } else if (url.includes("/payroll-runs")) {
        body = [];
      } else if (url.match(/\/employments\/\d+\/salary/)) {
        body = { annual_salary_pence: 4500000, hourly_rate_pence: 2308 };
      } else if (url.includes("/api/v1/employments")) {
        body = { items: employments, total: employments.length };
      }
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

const open = () => render(<MemoryRouter><Salary /></MemoryRouter>);

describe("Salary", () => {
  afterEach(cleanup);

  it("shows the effective-dated salary history on the card", async () => {
    stubFetch([]);
    open();
    // Header shows current salary; the history strip repeats it as
    // the latest dated revision.
    await waitFor(() =>
      expect(screen.getAllByText("£45,000.00").length).toBeGreaterThan(0));
    expect(screen.getByText(/from 6 Apr/)).toBeTruthy();
    expect(screen.getByText(/April pay rise/)).toBeTruthy();
    expect(screen.getByText(/from 1 Sept 2025/)).toBeTruthy();
  });

  it("New salary posts an effective-dated revision", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    open();
    await user.click(await screen.findByRole("button", { name: "New salary" }));
    await user.type(screen.getByLabelText(/Annual salary/), "48000");
    await user.click(screen.getByRole("button", { name: "Save salary" }));
    await waitFor(() => {
      const c = calls.find((x) =>
        x.method === "POST" && x.url.includes("/employments/1/salary-revisions"));
      expect(c).toBeDefined();
      const body = JSON.parse(c!.body);
      expect(body.annual_salary).toBe("48000");
      expect(body.effective_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    });
  });

  it("editing a revision patches it in place", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    open();
    await user.click(
      await screen.findByLabelText(/Edit salary from 6 Apr/));
    const input = screen.getByLabelText(/Annual salary/);
    expect((input as HTMLInputElement).value).toBe("45000");
    await user.clear(input);
    await user.type(input, "46000");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => {
      const c = calls.find((x) =>
        x.method === "PATCH" &&
        x.url.includes("/employments/1/salary-revisions/2"));
      expect(c).toBeDefined();
      expect(JSON.parse(c!.body).annual_salary).toBe("46000");
    });
  });

  it("deleting a revision removes it with an undo path", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    open();
    await user.click(
      await screen.findByLabelText(/Delete salary from 6 Apr/));
    await waitFor(() => expect(calls.some((x) =>
      x.method === "DELETE" &&
      x.url.includes("/employments/1/salary-revisions/2"))).toBe(true));
  });

  it("adding an employment can seed its first salary revision", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls, { employments: [] });
    const user = userEvent.setup();
    open();
    const [addBtn] = await screen.findAllByRole("button", { name: "Add employer" });
    await user.click(addBtn!);
    await user.type(screen.getByLabelText("Employer"), "New Corp");
    await user.type(screen.getByLabelText(/Annual salary/), "30000");
    // Page actions are aria-hidden behind the open dialog; the only
    // visible "Add employment" is the dialog's submit.
    await user.click(screen.getByRole("button", { name: "Add employment" }));
    await waitFor(() => {
      expect(calls.some((x) =>
        x.method === "POST" && x.url.endsWith("/employments"))).toBe(true);
      const employment = calls.find((x) =>
        x.method === "POST" && x.url.endsWith("/employments"));
      expect(employment).toBeDefined();
      expect(JSON.parse(employment!.body).initial_salary).toBe("30000");
    });
  });

  it("serializes a selected fixed payday without exposing JSON", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls, { employments: [] });
    const user = userEvent.setup();
    open();
    const [addBtn] = await screen.findAllByRole("button", { name: "Add employer" });
    await user.click(addBtn!);
    await user.type(screen.getByLabelText("Employer"), "Payday Corp");
    await user.selectOptions(screen.getByLabelText("Payday"), "fixed_day");
    await user.clear(screen.getByLabelText("Day of month"));
    await user.type(screen.getByLabelText("Day of month"), "31");
    await user.click(screen.getByRole("button", { name: "Add employment" }));
    await waitFor(() => {
      const call = calls.find((item) =>
        item.method === "POST" && item.url.endsWith("/employments"));
      expect(call).toBeDefined();
      expect(JSON.parse(call!.body).payday_rule).toEqual(expect.stringContaining('"kind":"fixed_day"'));
      expect(screen.queryByText(/"kind"/)).toBeNull();
    });
  });

  it("requires an explicit prior employer and confirmation for replacement", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    const second = { ...EMP, id: 2, employer: "Second Ltd" };
    stubFetch(calls, { employments: [EMP, second] });
    const user = userEvent.setup();
    open();
    await user.click(await screen.findByRole("button", { name: "Add employer" }));
    await user.type(screen.getByLabelText("Employer"), "Replacement Ltd");
    await user.click(screen.getByRole("radio", { name: /Replacement job/i }));
    expect((screen.getByRole("button", { name: "Add employment" }) as HTMLButtonElement).disabled).toBe(true);
    await user.selectOptions(screen.getByLabelText("Prior employment to replace"), "2");
    expect(screen.getByText(/Preview: Second Ltd will end/)).toBeTruthy();
    await user.click(screen.getByLabelText(/Confirm ending Second Ltd/));
    expect((screen.getByRole("button", { name: "Add employment" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
