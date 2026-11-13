import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Budget } from "../pages/Budget";

const budgets = [null, 7].map((owner, i) => ({ id: i + 1, owner_profile_id: owner, name: "November", period_type: "monthly", start_date: "2026-11-01", end_date: "2026-11-30", status: "locked" }));
function fixture(warning: string | null = null, zero = false) {
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = new URL(String(input), "http://localhost"); calls.push(url.pathname + url.search);
    const personal = url.pathname.includes("/budgets/2");
    const period = { start: warning ? "2026-11-01" : "2026-10-30", end: warning ? "2026-11-30" : "2026-11-26", basis: warning ? "calendar" : "pay_cycle", employment_id: warning ? null : 4, warning };
    const detail = { ...budgets[personal ? 1 : 0], read_only: true, lines: [], period: personal ? period : null };
    const actuals = { budget_id: personal ? 2 : 1, totals: {}, lines: {}, spent_pence: 0, as_of: "2026-11-13", spend_items: [], income_received_pence: 99999, external_income_pence: zero ? 0 : 12300, opening_funds_pence: zero ? 0 : -4500, contributions_pence: zero ? 0 : 8900, unattributed_internal_pence: zero ? 0 : 200, income_items: zero ? [] : [{ transaction_id: 77, date: "2026-10-30", account_id: 1, payee: "Received salary", category_id: null, amount_pence: 12300, contributor_profile_id: null }], contribution_items: zero ? [] : [{ transaction_id: 78, date: "2026-11-02", account_id: 2, payee: "Verified contribution", category_id: null, amount_pence: 8900, contributor_profile_id: 7 }] };
    let body: unknown = { items: [], total: 0 };
    if (url.pathname.endsWith("/households")) body = { items: [{id: 1, name: "Home"}], total: 1 };
    else if (url.pathname.endsWith("/profiles")) body = { items: [{ id: 7, display_name: "Member A", role: "admin", is_active: true }], total: 1 };
    else if (url.pathname.endsWith("/reports/combined")) body = { detail, actuals };
    else if (url.pathname.endsWith("/pay-cycle-settings")) body = { employment_id: null, options: [{ employment_id: 4, employer: "Employer" }] };
    else if (url.pathname.endsWith("/actuals")) body = actuals;
    else if (/\/budgets\/\d+$/.test(url.pathname)) body = detail;
    else if (url.pathname.endsWith("/budgets")) body = { items: budgets, total: 2 };
    else if (url.pathname.endsWith("/expected-movements")) body = [{ id: 30, expected_date: "2026-10-30", amount_pence: 12300, outstanding_pence: 0, dismissed_at: null, source_type: "employment", label: "Matched salary" }];
    return Promise.resolve(new Response(JSON.stringify(body), {headers: {"Content-Type":"application/json"}}));
  });
  return calls;
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });
it("uses effective personal cycle and complete funding outside manual plan", async () => {
  const calls = fixture(); const user = userEvent.setup(); render(<MemoryRouter><Budget /></MemoryRouter>);
  await user.click(await screen.findByRole("radio", { name: "Member A" }));
  await screen.findByText("Pay cycle: 30 Oct 2026 – 26 Nov 2026");
  expect(screen.getByRole("heading", {name: "November 2026"})).toBeTruthy();
  await waitFor(() => expect(calls.some(c => c.includes("/budgets/2?") && c.includes("basis=pay_cycle"))).toBe(true));
  expect(calls.some(c => c.includes("/budgets/2/actuals?") && c.includes("basis=pay_cycle"))).toBe(true);
  await waitFor(() => expect(calls.some(c => c.includes("expected-movements?") && c.includes("start=2026-10-30") && c.includes("end=2026-11-26"))).toBe(true));
  expect(screen.queryByText("Planned salary (forecast)")).toBeNull();
  expect(screen.getByText("Income received").parentElement!.parentElement!.textContent).toContain("£123.00");
  expect(screen.getByText("Opening funds").parentElement!.parentElement!.textContent).toContain("-£45.00");
  expect(screen.getByText("Planned spending").parentElement!.parentElement!.textContent).toContain("£0.00");
  expect(screen.getByRole("table", { name: "Received income", hidden: true }).closest("details")?.open).toBe(true);
  expect(screen.getByRole("table", {name: "Received income"}).textContent).toContain("Received salary");
  await user.click(screen.getByRole("radio", {name: "Shared"}));
  await screen.findByText("Net contributions");
  expect(screen.getByText("Net contributions").parentElement!.parentElement!.textContent).toContain("£89.00");
  expect(screen.getByText("Other external income").parentElement!.parentElement!.textContent).toContain("£123.00");
  expect(screen.getByRole("table", { name: "Contributions", hidden: true }).closest("details")?.open).toBe(true);
  expect(screen.getByRole("table", {name: "Contributions"}).textContent).toContain("Member A");
});
it("shows calendar fallback warning and does not select an arbitrary source", async () => {
  fixture("Choose a monthly pay source"); const user = userEvent.setup(); render(<MemoryRouter><Budget /></MemoryRouter>);
  await user.click(await screen.findByRole("radio", {name: "Member A"}));
  expect(await screen.findByText("Choose a monthly pay source")).toBeTruthy();
  expect(screen.queryByText(/^Pay cycle:/)).toBeNull();
  const source = await screen.findByRole("combobox", {name: "Pay-cycle source"});
  expect((source as HTMLSelectElement).value).toBe("");
  expect(within(source).getByRole("option", {name: "Employer"})).toBeTruthy();
});
it("preserves genuine zero income, opening funds and contributions over legacy totals", async () => {
  fixture(null, true); const user = userEvent.setup(); render(<MemoryRouter><Budget /></MemoryRouter>);
  await screen.findByText("Opening funds");
  expect(screen.getByText("Income received").parentElement!.parentElement!.textContent).toContain("£0.00");
  expect(screen.getByText("Opening funds").parentElement!.parentElement!.textContent).toContain("£0.00");
  await user.click(screen.getByRole("radio", {name: "Shared"}));
  await screen.findByText("Net contributions");
  expect(screen.getByText("Net contributions").parentElement!.parentElement!.textContent).toContain("£0.00");
  expect(screen.queryByText("Unattributed internal movements")).toBeNull();
});
it("saves only the explicitly selected source for the current owner", async () => {
  fixture("Choose a monthly pay source"); const user = userEvent.setup(); render(<MemoryRouter><Budget /></MemoryRouter>);
  await user.click(await screen.findByRole("radio", {name: "Member A"}));
  const source = await screen.findByRole("combobox", {name: "Pay-cycle source"});
  expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(0);
  await user.selectOptions(source, "4");
  await user.click(screen.getByRole("button", {name: "Save source"}));
  await waitFor(() => {
    const call = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === "PUT");
    expect(call).toBeTruthy();
    expect(JSON.parse(String(call![1]?.body))).toEqual({owner_profile_id: 7, employment_id: 4});
  });
});

it.each([false, true])("clears prior funding when source changes while new actuals are pending or fail (failure: %s)", async failure => {
  fixture();
  const original = vi.mocked(fetch).getMockImplementation()!;
  let saved = false;
  let finish: ((response: Response) => void) | undefined;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    if (init?.method === "PUT") saved = true;
    if (saved && url.includes("/budgets/2/actuals")) return new Promise<Response>(resolve => { finish = resolve; });
    const response = await original(input, init);
    if (saved && /\/budgets\/2\?/.test(url)) {
      const body = await response.json();
      return new Response(JSON.stringify({...body, period: {...body.period, start: "2026-10-28", end: "2026-11-25"}}), {headers: {"Content-Type": "application/json"}});
    }
    return response;
  });
  const user = userEvent.setup(); render(<MemoryRouter><Budget /></MemoryRouter>);
  await user.click(await screen.findByRole("radio", {name: "Member A"}));
  await screen.findByText("Received income details");
  expect(screen.getByRole("table", {name: "Received income"}).textContent).toContain("Received salary");
  expect(screen.getByText("Reconcile spending")).toBeTruthy();
  await user.selectOptions(screen.getByRole("combobox", {name: "Pay-cycle source"}), "4");
  await user.click(screen.getByRole("button", {name: "Save source"}));
  await screen.findByText("Pay cycle: 28 Oct 2026 – 25 Nov 2026");
  expect(screen.queryByText("Received income details")).toBeNull();
  expect(screen.queryByText("Income received")).toBeNull();
  expect(screen.queryByText("Reconcile spending")).toBeNull();
  await waitFor(() => expect(finish).toBeTruthy());
  if (failure) {
    finish!(new Response(JSON.stringify({detail: "New cycle actuals unavailable"}), {status: 400, headers: {"Content-Type": "application/json"}}));
    await screen.findAllByText(/New cycle actuals unavailable/);
    expect(screen.queryByText("Received income details")).toBeNull();
    expect(screen.queryByText("Income received")).toBeNull();
  } else {
    finish!(new Response(JSON.stringify({budget_id: 2, totals: {}, lines: {}, external_income_pence: 0, income_items: [], spent_pence: 0, opening_funds_pence: 200, opening_as_of: "2026-10-27"}), {headers: {"Content-Type": "application/json"}}));
    await screen.findByText("Income received");
    expect(screen.getByText("Income received").parentElement!.parentElement!.textContent).toContain("£0.00");
    expect(screen.getByText(/Balance through 27 Oct 2026/)).toBeTruthy();
  }
});

function fixedSettings(day: number | null) {
  fixture();
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation((input, init) => {
    if (String(input).includes("/pay-cycle-settings") && init?.method !== "PUT") {
      return Promise.resolve(new Response(JSON.stringify({employment_id: null, payday_day: day, options: []}), {headers: {"Content-Type": "application/json"}}));
    }
    return original(input, init);
  });
}
it("configures a fixed monthly payday without employment and validates the day before saving", async () => {
  fixedSettings(null); const user = userEvent.setup(); render(<MemoryRouter><Budget /></MemoryRouter>);
  await user.click(await screen.findByRole("radio", {name: "Member A"}));
  await user.selectOptions(await screen.findByRole("combobox", {name: "Pay-cycle source"}), "fixed");
  const day = screen.getByRole("spinbutton", {name: "Monthly payday"});
  const save = screen.getByRole("button", {name: "Save source"});
  expect((day as HTMLInputElement).value).toBe("");
  expect(save.hasAttribute("disabled")).toBe(true);
  await user.type(day, "32"); expect(save.hasAttribute("disabled")).toBe(true);
  await user.clear(day); await user.type(day, "1.5"); expect(save.hasAttribute("disabled")).toBe(true);
  await user.clear(day); await user.type(day, "11");
  expect(save.hasAttribute("disabled")).toBe(false);
  expect(screen.getByText(/Saturday or Sunday.*previous Friday/)).toBeTruthy();
  expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(0);
  await user.click(save);
  await waitFor(() => {
    const call = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({owner_profile_id: 7, employment_id: null, payday_day: 11});
  });
});
it("restores a fixed day from settings and can return to automatic without retaining it", async () => {
  fixedSettings(11); const user = userEvent.setup(); render(<MemoryRouter><Budget /></MemoryRouter>);
  await user.click(await screen.findByRole("radio", {name: "Member A"}));
  const source = await screen.findByRole("combobox", {name: "Pay-cycle source"});
  expect((source as HTMLSelectElement).value).toBe("fixed");
  expect((screen.getByRole("spinbutton", {name: "Monthly payday"}) as HTMLInputElement).value).toBe("11");
  await user.selectOptions(source, "");
  expect(screen.queryByRole("spinbutton", {name: "Monthly payday"})).toBeNull();
  await user.click(screen.getByRole("button", {name: "Save source"}));
  await waitFor(() => {
    const call = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({owner_profile_id: 7, employment_id: null});
  });
});
