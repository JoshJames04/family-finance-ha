/** Owner tabs must keep the chosen calendar period, even without a budget. */
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Budget } from "../pages/Budget";

const budgets = [
  { id: 1, owner_profile_id: null, name: "Shared November", period_type: "monthly", start_date: "2026-11-01", end_date: "2026-11-30", status: "approved" },
  { id: 2, owner_profile_id: 7, name: "Member November", period_type: "monthly", start_date: "2026-11-01", end_date: "2026-11-30", status: "approved" },
  { id: 3, owner_profile_id: 8, name: "Historical member", period_type: "monthly", start_date: "2025-12-01", end_date: "2025-12-31", status: "approved" },
];

function stubFetch(opts: { salary?: boolean; manualIncome?: boolean; singleOwner?: boolean } = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname;
    const detail = (b = budgets[0]!) => ({ ...b, lines: opts.manualIncome ? [
      { id: 91, category_id: 91, side: "income", original_pence: 250000, revised_pence: null, rollover: false, effective_pence: 250000 },
    ] : [], read_only: false });
    const actuals = { budget_id: 0, totals: {}, lines: {}, spent_pence: 0, income_received_pence: 12300, as_of: "2026-11-12", spend_items: [] };
    let body: unknown = { items: [], total: 0 };
    if (path.endsWith("/households")) body = { items: [{ id: 1, name: "Home" }], total: 1 };
    else if (path.endsWith("/profiles")) body = { items: [
      { id: 7, household_id: 1, display_name: "Member A", role: "admin", is_active: true },
      { id: 8, household_id: 1, display_name: "Member B", role: "member", is_active: true },
    ], total: 2 };
    else if (path.endsWith("/reports/combined")) body = { detail: { ...detail(), id: 0, read_only: true }, actuals: { ...actuals, income_received_pence: opts.singleOwner ? 45600 : 12300 } };
    else if (path.endsWith("/actuals")) body = actuals;
    else if (/\/budgets\/\d+$/.test(path)) body = detail(budgets.find(b => b.id === Number(path.split("/").at(-1)))!);
    else if (path.endsWith("/budgets")) body = opts.singleOwner ? { items: budgets.slice(0, 1), total: 1 } : { items: budgets, total: 3 };
    else if (path.endsWith("/categories")) body = { items: [{ id: 91, household_id: 1, parent_id: null, name: "Manual salary", is_system: false, archived: false }], total: 1 };
    else if (path.includes("expected-movements")) body = opts.salary && path.endsWith("/expected-movements") && url.searchParams.get("budget_id") !== "1" ? [
      { id: 41, household_id: 1, account_id: 1, expected_date: "2026-11-27", amount_pence: 240000, outstanding_pence: 240000, dismissed_at: null, label: "Employer forecast", confidence: "estimated", source_type: "employment", source_id: 4 },
      { id: 42, household_id: 1, account_id: 1, expected_date: "2026-11-28", amount_pence: 20000, outstanding_pence: 10000, dismissed_at: null, label: "Payroll forecast", confidence: "scheduled", source_type: "payroll_run", source_id: 5 },
      { id: 43, household_id: 1, account_id: 1, expected_date: "2026-11-29", amount_pence: -5000, outstanding_pence: 5000, dismissed_at: null, label: "Salary adjustment", confidence: "estimated", source_type: "employment", source_id: 6 },
      { id: 44, household_id: 1, account_id: 1, expected_date: "2026-11-29", amount_pence: 7000, outstanding_pence: 7000, dismissed_at: null, label: "Other receipt", confidence: "estimated", source_type: "manual", source_id: 7 },
      { id: 45, household_id: 1, account_id: 1, expected_date: "2026-11-10", amount_pence: 300000, outstanding_pence: 0, dismissed_at: null, label: "Already received salary", confidence: "scheduled", source_type: "payroll_run", source_id: 8 },
      { id: 46, household_id: 1, account_id: 1, expected_date: "2026-11-29", amount_pence: 200000, outstanding_pence: 200000, dismissed_at: "2026-11-11T12:00:00Z", label: "Dismissed salary", confidence: "estimated", source_type: "employment", source_id: 9 },
    ] : [];
    return Promise.resolve(new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } }));
  });
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("uses all-account combined actuals even when only a shared budget exists", async () => {
  stubFetch({ singleOwner: true });
  const user = userEvent.setup();
  render(<MemoryRouter><Budget /></MemoryRouter>);
  await screen.findByText("Combined view — read-only");
  expect(screen.getByText("Income received").parentElement!.parentElement!.textContent).toContain("£456.00");
  await user.click(screen.getByRole("radio", { name: "Shared" }));
  await screen.findByText("approved");
  expect(screen.getByText("Income received").parentElement!.parentElement!.textContent).toContain("£123.00");
});

it("keeps November 2026 across All, same-period member, missing member and Shared tabs", async () => {
  stubFetch();
  const user = userEvent.setup();
  render(<MemoryRouter><Budget /></MemoryRouter>);
  const picker = await screen.findByRole("combobox", { name: "Budget", hidden: true });
  await waitFor(() => expect((picker as HTMLSelectElement).value).not.toBe(""));
  await user.selectOptions(picker, "1");
  expect(screen.getByRole("radio", { name: "All" }).getAttribute("aria-checked")).toBe("true");
  await user.click(screen.getByRole("radio", { name: "Member A" }));
  await waitFor(() => expect((picker as HTMLSelectElement).value).toBe("2"));
  await user.click(screen.getByRole("radio", { name: "Member B" }));
  expect((screen.getByRole("combobox", { name: "Year", hidden: true }) as HTMLSelectElement).value).toBe("2026");
  expect(within(screen.getByRole("group", { name: "Month", hidden: true })).getByRole("button", { name: "Nov", hidden: true }).getAttribute("aria-pressed")).toBe("true");
  expect(await screen.findByText("No budget for this owner in November 2026")).toBeTruthy();
  expect(screen.queryByText("Reconcile spending")).toBeNull();
  expect(screen.queryByText("Planned spending")).toBeNull();
  expect(screen.queryByRole("heading", { name: "December 2025" })).toBeNull();
  expect((picker as HTMLSelectElement).selectedOptions[0]?.textContent).toContain("November 2026");
  await user.click(screen.getByRole("radio", { name: "All" }));
  await screen.findByText("Combined view — read-only");
  expect((picker as HTMLSelectElement).value).toBe("1");
  await user.click(screen.getByRole("radio", { name: "Member B" }));
  await screen.findByText("No budget for this owner in November 2026");
  await user.click(screen.getByRole("radio", { name: "Shared" }));
  await screen.findByRole("heading", { name: "November 2026" });
  expect((picker as HTMLSelectElement).value).toBe("1");
  await user.click(screen.getByRole("radio", { name: "All" }));
  await screen.findByText("Combined view — read-only");
  expect((picker as HTMLSelectElement).value).toBe("1");
});

it.each([false, true])("shows salary forecasts separately from received and manual income (manual plan: %s)", async (manualIncome) => {
  stubFetch({ salary: true, manualIncome });
  const user = userEvent.setup();
  render(<MemoryRouter><Budget /></MemoryRouter>);
  const picker = await screen.findByRole("combobox", { name: "Budget", hidden: true });
  await waitFor(() => expect((picker as HTMLSelectElement).value).not.toBe(""));
  await user.selectOptions(picker, "1");
  const forecastLabel = await screen.findByText("Planned salary (forecast)");
  expect(forecastLabel.parentElement!.parentElement!.textContent).toContain("£2,500.00");
  const received = screen.getByText("Income received").parentElement!.parentElement!;
  expect(received.textContent).toContain("£123.00");
  if (manualIncome) expect(received.textContent).toContain("of £2,500.00 planned");
  const income = screen.getByRole("heading", { name: "Income" }).closest("section")!;
  const expected = within(income).getByRole("list", { name: "Expected salary" });
  expect(within(expected).getByText("Employer forecast")).toBeTruthy();
  expect(within(expected).getByText("Payroll forecast")).toBeTruthy();
  expect(within(expected).queryByText("Salary adjustment")).toBeNull();
  expect(within(expected).queryByText("Other receipt")).toBeNull();
  expect(within(expected).queryByText("Already received salary")).toBeNull();
  expect(within(expected).queryByText("Dismissed salary")).toBeNull();
  expect(within(expected).getByText("£100.00")).toBeTruthy();
  expect(within(expected).queryByText("£200.00")).toBeNull();
  expect(within(expected).getByText(/27 Nov/)).toBeTruthy();
  expect(within(expected).queryByRole("button")).toBeNull();
  if (manualIncome) {
    const manual = within(income).getByText("Manual salary").closest("tr")!;
    expect(manual.textContent).toContain("£2,500.00");
    expect(manual.textContent).not.toContain("£5,000.00");
  }
  await user.click(screen.getByRole("radio", { name: "Shared" }));
  await waitFor(() => expect(screen.queryByText("Planned salary (forecast)")).toBeNull());
  expect(screen.queryByRole("list", { name: "Expected salary" })).toBeNull();
  expect(screen.getByText("Income received").parentElement!.parentElement!.textContent).toContain("£123.00");
});

it.each(["previous", "year", "mobile"])("allows explicit %s navigation from an owner's missing period", async (navigation) => {
  stubFetch();
  const user = userEvent.setup();
  render(<MemoryRouter><Budget /></MemoryRouter>);
  const picker = await screen.findByRole("combobox", { name: "Budget", hidden: true });
  await waitFor(() => expect((picker as HTMLSelectElement).value).not.toBe(""));
  await user.selectOptions(picker, "1");
  await user.click(screen.getByRole("radio", { name: "Member B" }));
  await screen.findByText("No budget for this owner in November 2026");
  if (navigation === "previous") await user.click(screen.getByRole("button", { name: "Previous budget" }));
  else if (navigation === "year") await user.selectOptions(screen.getByRole("combobox", { name: "Year", hidden: true }), "2025");
  else await user.selectOptions(picker, "3");
  await screen.findByRole("heading", { name: "December 2025" });
  expect((picker as HTMLSelectElement).value).toBe("3");
  expect(screen.getByRole("radio", { name: "Member B" }).getAttribute("aria-checked")).toBe("true");
});
