import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { widgetById } from "../widgets/registry";
import { Salary } from "../pages/Salary";
import { Budget } from "../pages/Budget";
import { Transactions } from "../pages/Transactions";
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const budget = { id: 1, start_date: "2020-01-01", end_date: "2030-12-31", owner_profile_id: null, lines: [{ id: 5, side: "expense", category_id: 1, original_pence: 10000 }] };
function stub(overrides: (url: string) => Response | Promise<Response> | undefined) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(input => {
    const url = String(input);
    const overridden = overrides(url);
    if (overridden) return Promise.resolve(overridden);
    const body = url.includes("households") ? { items: [{ id: 1, name: "Home" }] }
      : url.includes("/budgets/1/actuals") ? { lines: { "5": { actual_pence: -2500, effective_pence: 10000 } } }
      : url.includes("/budgets/1") ? budget
      : url.includes("/budgets") ? { items: [budget] }
      : url.includes("/categories") ? { items: [{ id: 1, name: "Food" }] }
      : url.includes("annualised") ? { total_annualised_pence: 12000 }
      : { items: [], total: 0 };
    return Promise.resolve(response(body));
  });
}
function widget(id: string) { const W = widgetById(id)!.Component; return render(<MemoryRouter><W title="Test widget" /></MemoryRouter>); }
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });
it("report failure surfaces Retry while successful subscriptions remain visible", async () => {
  stub(url => url.includes("reports/monthly") ? response({ detail: "Report unavailable" }, 400) : undefined);
  widget("kpis");
  expect((await screen.findByRole("alert")).textContent).toContain("Report unavailable");
  expect(screen.getByText("Subscriptions")).toBeTruthy();
  expect(screen.queryByText("Income this month")).toBeNull();
});
it("subscription failure surfaces Retry while successful report remains visible", async () => {
  stub(url => url.includes("annualised") ? response({ detail: "Subscriptions unavailable" }, 400) : url.includes("reports/monthly") ? response({ sections: [{ key: "income", figures: [{ key: "income_total", amount_pence: 123400 }] }] }) : undefined);
  widget("kpis");
  expect((await screen.findByRole("alert")).textContent).toContain("Subscriptions unavailable");
  expect(screen.getByText("Income this month")).toBeTruthy();
  expect(screen.queryByText("Subscriptions")).toBeNull();
});
for (const id of ["budget", "budget-categories"]) {
  it(`${id} reports actuals failure and retries actuals instead of displaying zero`, async () => {
    let fail = true;
    stub(url => url.includes("/actuals") && fail ? response({ detail: "Actuals unavailable" }, 400) : undefined);
    widget(id);
    expect((await screen.findByRole("alert")).textContent).toContain("Actuals unavailable");
    expect(screen.queryByText("0%")).toBeNull();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(await screen.findByText(id === "budget" ? "25%" : "Food")).toBeTruthy();
  });
  it(`${id} finishes an empty successful budget list`, async () => {
    stub(url => /\/budgets(?:\?|$)/.test(url) ? response({ items: [] }) : undefined);
    widget(id);
    expect(await screen.findByText(/No budget/)).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
  });
  it(`${id} exposes budget list failure despite gated detail`, async () => {
    stub(url => /\/budgets(?:\?|$)/.test(url) ? response({ detail: "Budgets unavailable" }, 400) : undefined);
    widget(id);
    expect((await screen.findByRole("alert")).textContent).toContain("Budgets unavailable");
  });
}
it("pagination appends a newly settled page once and retains prior rows while pending", async () => {
  let finish!: (r: Response) => void;
  const rows = (start: number) => Array.from({ length: 200 }, (_, i) => ({ id: start + i, account_id: 1, date: "2026-04-01", amount_pence: -100, payee: `Shop ${start + i}`, status: "cleared", splits: [] }));
  stub(url => url.includes("/transactions") ? new URL(url, "http://local").searchParams.get("page") === "2" ? new Promise(resolve => { finish = resolve; }) : response({ items: rows(1), total: 600, page: 1, size: 200 }) : undefined);
  render(<MemoryRouter><Transactions /></MemoryRouter>);
  expect(await screen.findByText("Showing 200 of 600")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Load more" }));
  await waitFor(() => expect(finish).toBeDefined());
  expect(screen.getByText("Showing 200 of 600")).toBeTruthy();
  finish(response({ items: rows(201), total: 600, page: 2, size: 200 }));
  expect(await screen.findByText("Showing 400 of 600")).toBeTruthy();
});
it("safe-to-spend labels the chosen account and makes absent payday account-specific", async () => {
  stub(url => /\/accounts(?:\?|$)/.test(url) ? response({ items: [{ id: 1, name: "Joint", archived: false }, { id: 2, name: "Salary", archived: false }] }) : url.includes("/projection") ? response({ account_id: 1, safe_to_spend_pence: 10000, start_balance_pence: 10000, committed_until_payday_pence: 0, next_payday: null }) : undefined);
  widget("safe-to-spend");
  expect(await screen.findByText(/No payday scheduled for Joint/)).toBeTruthy();
  expect(screen.queryByText("Set up payday")).toBeNull();
});

it("budget summary never presents missing actuals as zero", async () => {
  stub(url => url.includes("/actuals") ? response({detail: "Actuals unavailable"}, 400) : url.includes("expected-movements") ? response([]) : undefined);
  render(<MemoryRouter><Budget /></MemoryRouter>);
  await screen.findAllByRole("alert");
  expect(screen.queryByText("Spent so far")).toBeNull();
  expect(screen.queryByText("Income received")).toBeNull();
});
it("budget expected movements follow selected owner even within the same period", async () => {
  const shared = {...budget, owner_profile_id: null, status: "approved"};
  const personal = {...shared, id: 2, owner_profile_id: 2};
  stub(url => url.includes("expected-movements") ? response([{id: 1, label: new URL(url, "http://local").searchParams.get("budget_id") === "2" ? "Personal payday" : "Shared bill", expected_date: "2026-11-20", amount_pence: 1000}]) : /\/budgets(?:\?|$)/.test(url) ? response({items:[shared, personal]}) : url.includes("/budgets/2/actuals") ? response({lines:{}}) : url.includes("/budgets/2") ? response(personal) : url.includes("/budgets/1") && !url.includes("actuals") ? response(shared) : undefined);
  render(<MemoryRouter><Budget /></MemoryRouter>);
  expect(await screen.findByText("Shared bill")).toBeTruthy();
  fireEvent.click(screen.getByRole("radio", {name:"Member 2"}));
  expect(await screen.findByText("Personal payday")).toBeTruthy();
  expect(screen.queryByText("Shared bill")).toBeNull();
});


it("selecting salary account shows its payday without mixing the joint balance", async () => {
  stub(url => /\/accounts(?:\?|$)/.test(url) ? response({items:[{id:1,name:"Joint",archived:false},{id:2,name:"Salary",archived:false}]}) : url.includes("/projection") ? response({account_id: url.includes("/2/") ? 2 : 1, safe_to_spend_pence: url.includes("/2/") ? 20000 : 10000, start_balance_pence:10000, committed_until_payday_pence:0, next_payday: url.includes("/2/") ? "2026-11-28" : null}) : undefined);
  widget("safe-to-spend");
  expect(await screen.findByText(/No payday scheduled for Joint/)).toBeTruthy();
  fireEvent.change(screen.getByRole("combobox", {name:"Safe to spend account"}), {target:{value:"2"}});
  expect(await screen.findByText(/Payday 28 Nov/)).toBeTruthy();
  expect(screen.queryByText(/No payday scheduled for Joint/)).toBeNull();
  expect(screen.getByText("£200")).toBeTruthy();
});

it("salary names missing forecast prerequisites and offers dated tax-code repair", async () => {
  const emp = {id:1, employer:"Acme", profile_id:1, start_date:"2025-01-01", end_date:null, deduction_mode:"paye", contracted_hours:37.5, jurisdiction:"england_wales", payday_rule:null, paid_into_account_id:null};
  let saved: unknown;
  vi.spyOn(globalThis,"fetch").mockImplementation((input,init) => {
    const url=String(input);
    if(init?.method === "POST") { saved=JSON.parse(String(init.body)); return Promise.resolve(response({id:1})); }
    return Promise.resolve(response(url.includes("households") ? {items:[{id:1,name:"Home"}]} : url.includes("salary-revisions") ? [{effective_date:"2099-01-01",annual_salary_pence:3000000}] : url.includes("tax-code-revisions") || url.includes("payroll-runs") ? [] : /\/employments(?:\?|$)/.test(url) ? {items:[emp]} : {items:[]}));
  });
  render(<MemoryRouter><Salary /></MemoryRouter>);
  expect(await screen.findByText(/Missing salary effective/)).toBeTruthy();
  expect(screen.getByText(/Missing tax code effective/)).toBeTruthy();
  expect(screen.getByText(/Missing payday rule/)).toBeTruthy();
  expect(await screen.findByText(/Missing active paid-into account/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"Add tax code"}));
  fireEvent.change(screen.getByLabelText("Tax code"),{target:{value:"1257L"}});
  fireEvent.change(screen.getByLabelText("Effective from"),{target:{value:"2026-11-01"}});
  fireEvent.click(screen.getByRole("button",{name:"Save tax code"}));
  await waitFor(()=>expect(saved).toEqual({tax_code:"1257L",effective_date:"2026-11-01"}));
});


it("gross-only piecework does not ask for salary or PAYE tax setup", async () => {
  const emp = {id:1, employer:"Shifts", profile_id:1, start_date:"2025-01-01", end_date:null, employment_type:"piecework", deduction_mode:"gross_only", contracted_hours:0, jurisdiction:"england_wales", payday_rule:"last_working_day", paid_into_account_id:1};
  stub(url => url.includes("salary-revisions") || url.includes("payroll-runs") ? response([]) : /\/employments(?:\?|$)/.test(url) ? response({items:[emp]}) : /\/accounts(?:\?|$)/.test(url) ? response({items:[{id:1,archived:false}]}) : undefined);
  render(<MemoryRouter><Salary /></MemoryRouter>);
  expect(await screen.findByText(/Piecework income appears/)).toBeTruthy();
  expect(screen.queryByText(/Missing salary effective/)).toBeNull();
  expect(screen.queryByText(/Missing tax code effective/)).toBeNull();
});

it("owner switch does not relabel retained expected movements while the new scope is pending", async () => {
  const shared = {...budget, status:"approved"}, personal={...shared,id:2,owner_profile_id:2};
  let requested = false;
  stub(url => url.includes("expected-movements") ? new URL(url,"http://local").searchParams.get("budget_id") === "2" ? (requested=true, new Promise<Response>(()=>{})) : response([{id:1,label:"Shared pending bill",expected_date:"2026-11-20",amount_pence:-1000}]) : /\/budgets(?:\?|$)/.test(url) ? response({items:[shared,personal]}) : url.includes("/budgets/2/actuals") ? response({lines:{}}) : url.includes("/budgets/2") ? response(personal) : url.includes("/budgets/1") && !url.includes("actuals") ? response(shared) : undefined);
  render(<MemoryRouter><Budget /></MemoryRouter>);
  await screen.findByText("Shared pending bill");
  fireEvent.click(screen.getByRole("radio",{name:"Member 2"}));
  await waitFor(()=>expect(requested).toBe(true));
  expect(screen.queryByText("Shared pending bill")).toBeNull();
});

it("owner switch hides prior budget detail and actuals until each new resource settles", async () => {
  const shared={...budget,status:"approved"}, personal={...shared,id:2,owner_profile_id:2,status:"locked",lines:[{id:6,category_id:1,side:"expense",original_pence:20000}]};
  let detailRequested=false, actualsRequested=false;
  let finishDetail!: (r:Response)=>void, finishActuals!: (r:Response)=>void;
  stub(url => /\/budgets(?:\?|$)/.test(url) ? response({items:[shared,personal]}) : url.includes("/budgets/2/actuals") ? (actualsRequested=true,new Promise(resolve=>{finishActuals=resolve})) : url.includes("/budgets/2") ? (detailRequested=true,new Promise(resolve=>{finishDetail=resolve})) : url.includes("/budgets/1")&&!url.includes("actuals") ? response(shared) : url.includes("expected-movements") ? response([]) : undefined);
  render(<MemoryRouter><Budget /></MemoryRouter>);
  await screen.findByText("Food");
  expect(screen.getAllByText("£25.00").length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("radio",{name:"Member 2"}));
  await waitFor(()=>expect(detailRequested&&actualsRequested).toBe(true));
  expect(screen.queryByText("Food")).toBeNull();
  expect(screen.queryByText("£25.00")).toBeNull();
  finishDetail(response(personal));
  await screen.findByText("locked — read-only");
  await waitFor(()=>expect(screen.queryByText("Spent so far")).toBeNull());
  expect(screen.queryByText("£25.00")).toBeNull();
  finishActuals(response({lines:{"6":{actual_pence:-5000,effective_pence:20000}}}));
  await screen.findByText("Food");
  expect(screen.getAllByText("£50.00").length).toBeGreaterThan(0);
});
