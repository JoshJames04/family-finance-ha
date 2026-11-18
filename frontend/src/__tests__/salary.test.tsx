/** #296: salary is an effective-dated series — the card shows the
 * dated history, "New salary" records a revision from a date, and
 * adding an employment can seed its first revision. */
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Salary } from "../pages/Salary";
import { undoToast } from "../lib/toast";

vi.mock("../lib/toast", () => ({ undoToast: vi.fn() }));

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
    annual_salary_pence: 4500000, shares_owned: "123.4567", notes: "April pay rise" },
  { id: 1, employment_id: 1, effective_date: "2025-09-01",
    annual_salary_pence: 4200000, notes: null },
];

function stubFetch(calls: { url: string; method: string; body: string }[],
  opts: { employments?: object[]; revisionsReady?: Promise<void>; payrollRuns?: object[]; payslips?: object[] } = {}) {
  const employments = opts.employments ?? [EMP];
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: String(init?.body ?? "") });
      let body: object | object[] = { items: [], total: 0 };
      if (method === "POST" && url.endsWith("/employments")) {
        body = { ...EMP, id: 7 };
      } else if (method === "PATCH" && url.includes("/payslips/")) {
        body = { ...(opts.payslips?.[0] ?? {}),
          extracted: {components:JSON.parse(String(init?.body ?? '{}')).components} };
      } else if (method !== "GET") {
        body = { ok: true };
      } else if (url.includes("/api/v1/households")) {
        body = { items: [{ id: 1, name: "Home" }], total: 1 };
      } else if (url.includes("/api/v1/profiles")) {
        body = { items: PROFILES, total: PROFILES.length };
      } else if (url.includes("/bonuses")) {
        body = [];
      } else if (url.includes("/tax-code-revisions")) {
        body = [];
      } else if (url.includes("/salary-revisions")) {
        body = REVISIONS;
        if (opts.revisionsReady) {
          return opts.revisionsReady.then(() => new Response(JSON.stringify(body), {
            status: 200, headers: { "Content-Type": "application/json" },
          }));
        }

      } else if (url.includes("/payslips")) {
        body = opts.payslips ?? [];
      } else if (url.includes("/payroll-runs")) {
        body = opts.payrollRuns ?? [];
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
    expect(screen.getByText(/123.4567 shares/)).toBeTruthy();
    expect(screen.getByText(/0 shares/)).toBeTruthy();
    expect(screen.getByText(/from 1 Sept 2025/)).toBeTruthy();
  });

  it("New salary posts an effective-dated revision", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    open();
    await user.click(await screen.findByRole("button", { name: "New salary" }));
    await user.type(screen.getByLabelText(/Annual salary/), "48000");
    const shares = screen.getByLabelText("Shares owned");
    expect((shares as HTMLInputElement).value).toBe("123.4567");
    await user.clear(shares);
    await user.type(shares, "200.1234");
    await user.click(screen.getByRole("button", { name: "Save salary" }));
    await waitFor(() => {
      const c = calls.find((x) =>
        x.method === "POST" && x.url.includes("/employments/1/salary-revisions"));
      expect(c).toBeDefined();
      const body = JSON.parse(c!.body);
      expect(body.annual_salary).toBe("48000");
      expect(body.shares_owned).toBe("200.1234");
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
      expect(JSON.parse(c!.body).shares_owned).toBe("123.4567");
    });
  });

  it("waits for effective holdings before saving a new salary opened during history loading", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    let release!: () => void;
    stubFetch(calls, { revisionsReady: new Promise<void>((resolve) => { release = resolve; }) });
    const user = userEvent.setup();
    open();
    await user.click(await screen.findByRole("button", { name: "New salary" }));
    await user.type(screen.getByLabelText(/Annual salary/), "48000");
    expect((screen.getByRole("button", { name: "Save salary" }) as HTMLButtonElement).disabled).toBe(true);
    release();
    await waitFor(() => expect((screen.getByLabelText("Shares owned") as HTMLInputElement).value).toBe("123.4567"));
    await user.click(screen.getByRole("button", { name: "Save salary" }));
    await waitFor(() => {
      const saved = calls.find((call) => call.method === "POST" && call.url.endsWith("/salary-revisions"));
      expect(saved).toBeDefined();
      expect(JSON.parse(saved!.body).shares_owned).toBe("123.4567");
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
    await vi.mocked(undoToast).mock.calls.at(-1)![1]();
    const restored = calls.find((x) => x.method === "POST" && x.url.endsWith("/salary-revisions"));
    expect(JSON.parse(restored!.body).shares_owned).toBe("123.4567");
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
    await user.clear(screen.getByLabelText("Initial shares owned"));
    await user.type(screen.getByLabelText("Initial shares owned"), "250.125");
    await user.selectOptions(screen.getByLabelText("Share payment method"), "payroll_noncash");
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
      expect(JSON.parse(employment!.body).initial_shares_owned).toBe("250.125");
      expect(JSON.parse(employment!.body).share_payment_method).toBe("payroll_noncash");
    });
  });

  it("rejects negative holdings and more than four decimal places", async () => {
    stubFetch([]);
    const user = userEvent.setup();
    open();
    await user.click(await screen.findByRole("button", { name: "New salary" }));
    await user.type(screen.getByLabelText(/Annual salary/), "48000");
    const shares = screen.getByLabelText("Shares owned");
    for (const value of ["-1", "1.12345"]) {
      await user.clear(shares);
      await user.type(shares, value);
      expect((screen.getByRole("button", { name: "Save salary" }) as HTMLButtonElement).disabled).toBe(true);
    }
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


describe("Payroll cutoff", () => {
  afterEach(cleanup);
  it("preserves cutoff when changing payday and lets the employer edit it", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls, { employments: [{ ...EMP, payday_rule: JSON.stringify({kind: "fixed_day", day: 25, cutoff_day: 14}) }] });
    const user = userEvent.setup();
    open();
    await user.click(await screen.findByLabelText("Edit Acme Ltd"));
    const cutoff = screen.getByLabelText("Payroll cutoff day") as HTMLInputElement;
    expect(cutoff.value).toBe("14");
    await user.selectOptions(screen.getByLabelText("Payday"), "nth_weekday");
    expect(cutoff.value).toBe("14");
    await user.clear(cutoff);
    await user.type(cutoff, "18");
    await user.click(screen.getByRole("button", {name: "Save changes"}));
    await waitFor(() => {
      const call = calls.find(c => c.method === "PATCH" && c.url.endsWith("/employments/1"));
      expect(call).toBeDefined();
      expect(JSON.parse(JSON.parse(call!.body).payday_rule).cutoff_day).toBe(18);
    });
  });
});

describe("Bonus placement", () => {
  afterEach(() => {cleanup(); vi.restoreAllMocks();});
  it("offers Add bonus beside New salary in employer configuration only", async () => {
    stubFetch([]);
    const user = userEvent.setup();
    open();
    const bonus = await screen.findByRole("button", {name:"Add bonus"});
    expect(bonus.parentElement).toBe(screen.getByRole("button", {name:"New salary"}).parentElement);
    await user.click(bonus);
    expect(screen.getByRole("dialog", {name:"Add bonus — Acme Ltd"})).toBeTruthy();
    await user.click(screen.getByRole("button", {name:"Cancel"}));
    await user.click(screen.getByRole("button", {name:"Breakdown"}));
    expect(screen.queryByRole("button", {name:"Add bonus"})).toBeNull();

  });
});

const GROSS_ONLY_RUN = {id:5, employment_id:1, period_start:'2026-04-01',
  period_end:'2026-04-30', pay_date:'2026-04-28', status:'forecast', tax_year:'2026-27',
  rule_set_year:null, rule_set_version:0, gross_pence:100000, net_pence:100000,
  components:[{id:1,kind:'base_salary',forecast_pence:100000,actual_pence:null}]};

describe('Gross-only confirmation paths', () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('passes gross-only mode through the manual reconciliation dialog', async () => {
  stubFetch([], {employments:[{...EMP,deduction_mode:'gross_only'}], payrollRuns:[GROSS_ONLY_RUN]});
  const user = userEvent.setup();
  open();
  await user.click(await screen.findByRole('button',{name:'Breakdown'}));
  await user.click(await screen.findByRole('button',{name:'Show payroll details for 2026-04-28'}));
  await user.click(await screen.findByRole('button',{name:'Check amounts'}));
  expect(screen.getByLabelText('Taxable pay this period (optional)')).toBeTruthy();
});

it('passes gross-only mode through uploaded payslip confirmation', async () => {
  stubFetch([], {employments:[{...EMP,deduction_mode:'gross_only'}], payrollRuns:[GROSS_ONLY_RUN],
    payslips:[{id:1,payroll_run_id:5,original_name:'synthetic.pdf',size_bytes:100,
      media_type:'application/pdf',status:'reviewed',extracted:{},warnings:[],overtime_findings:[]}]});
  const user = userEvent.setup();
  open();
  await user.click(await screen.findByRole('button',{name:'Breakdown'}));
  await user.click(await screen.findByRole('button',{name:'Payslip'}));
  await user.click(await screen.findByRole('button',{name:'Review'}));
  expect(screen.getByLabelText('Taxable pay this period (optional)')).toBeTruthy();
});

});


describe('Uploaded payslip completeness', () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });
  it('blocks partial OCR locally until every component has an explicit amount', async () => {
    const calls: { url:string; method:string; body:string }[] = [];
    stubFetch(calls, {employments:[{...EMP,deduction_mode:'gross_only'}], payrollRuns:[GROSS_ONLY_RUN],
      payslips:[{id:1,payroll_run_id:5,original_name:'synthetic.pdf',size_bytes:100,
        media_type:'application/pdf',status:'reviewed',extracted:{components:{base_salary:100000}},
        warnings:[],overtime_findings:[]}]});
    const user = userEvent.setup(); open();
    await user.click(await screen.findByRole('button',{name:'Breakdown'}));
    await user.click(await screen.findByRole('button',{name:'Payslip'}));
    await user.click(await screen.findByRole('button',{name:'Review'}));
    await user.click(screen.getByRole('button',{name:'Save review'}));
    const saved = calls.find(c => c.method === 'PATCH' && c.url.includes('/payslips/'));
    expect(saved).toBeDefined();
    expect(JSON.parse(saved!.body).components).toEqual({base_salary:100000});
    await user.type(screen.getByLabelText('Payslip gross'),'1000');
    await user.type(screen.getByLabelText('Payslip net entitlement'),'1000');
    await user.click(screen.getByRole('button',{name:'Confirm actuals'}));
    expect(calls.some(c => c.method === 'POST' && c.url.endsWith('/confirm'))).toBe(false);
    for (const label of ['Overtime','Bonus','Allowance','Unpaid leave','Pension','Tax',
      'National Insurance','Student loan']) await user.type(screen.getByLabelText(label),'0');
    const noncash = screen.queryByLabelText('Non-cash share award');
    if (noncash) await user.type(noncash,'0');
    await user.click(screen.getByRole('button',{name:'Confirm actuals'}));
    await waitFor(() => {
      const request = calls.find(c => c.method === 'POST' && c.url.endsWith('/confirm'));
      expect(request).toBeDefined();
      const body = JSON.parse(request!.body);
      expect(body.actuals.base_salary).toBe(100000);
      expect(body.actuals.overtime).toBe(0);
      expect(Object.keys(body.actuals).length).toBeGreaterThanOrEqual(9);
      expect(body.taxable_pence).toBeUndefined();
    });
  });
});

describe("Automatic compact payroll", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });
  it("refreshes on opening Breakdown and groups cutoff payroll by pay month", async () => {
    const calls: {url:string;method:string;body:string}[] = [];
    stubFetch(calls, {payrollRuns:[{...GROSS_ONLY_RUN, period_start:'2026-03-16',
      period_end:'2026-04-15', statutory_pay_date:'2026-04-28'}]});
    const user = userEvent.setup();
    open();
    await user.click(await screen.findByRole('button',{name:'Breakdown'}));
    expect(await screen.findByLabelText('Payroll year')).toBeTruthy();
    await waitFor(() => expect(calls.some(c => c.method === 'POST' && c.url.endsWith('/1/refresh-payroll'))).toBe(true));
    const summary = screen.getByRole('region', {name:'Monthly payroll summary'});
    expect(within(summary).getByText('2026-04')).toBeTruthy();
    expect(within(summary).queryByText('2026-03')).toBeNull();
    expect(screen.queryByRole('button',{name:'Add run'})).toBeNull();
    expect(screen.queryByRole('button',{name:'Review forecasts'})).toBeNull();
  });
});
describe("Historical payroll links", () => {
  afterEach(() => {cleanup(); vi.restoreAllMocks();});
  it("selects the linked payroll year instead of hiding an old run", async () => {
    stubFetch([], {payrollRuns:[{...GROSS_ONLY_RUN, pay_date:'2025-04-28', statutory_pay_date:'2025-04-28', period_start:'2025-04-01', period_end:'2025-04-30'}]});
    render(<MemoryRouter initialEntries={['/?run_id=5']}><Salary /></MemoryRouter>);
    await waitFor(() => expect((screen.getByLabelText('Payroll year') as HTMLSelectElement).value).toBe('2025'));
    expect(screen.getByRole('button', {name:'Payslip'})).toBeTruthy();
  });
});
describe("Compact employer rows", () => {
  afterEach(() => {cleanup(); vi.restoreAllMocks();});
  it("keeps optional amount checking and gross breakdown inside the expanded detail", async () => {
    stubFetch([], {payrollRuns:[GROSS_ONLY_RUN]});
    const user = userEvent.setup();
    open();
    await user.click(await screen.findByRole('button',{name:'Breakdown'}));
    expect(screen.queryByRole('button',{name:'Check amounts'})).toBeNull();
    await user.click(await screen.findByRole('button',{name:'Show payroll details for 2026-04-28'}));
    expect(await screen.findByRole('button',{name:'Check amounts'})).toBeTruthy();
    expect(screen.getByText('Forecast gross')).toBeTruthy();
  });

  it.each(['forecast', 'reconciled'])("combines overtime hours at matching rates in %s records", async status => {
    const line = (id:number, label:string, amount:number) => ({id, kind:'overtime', label,
      forecast_pence:amount, actual_pence:status === 'reconciled' ? amount : null,
      variance_pence:status === 'reconciled' ? 0 : null});
    const lines = [line(1,'8 hours @ £12.345',9876), line(2,'3 hours @ £16.46',4938),
      line(3,'4 hours @ £12.345',4938)];
    stubFetch([], {payrollRuns:[{...GROSS_ONLY_RUN, status, components:lines}]});
    const user = userEvent.setup(); open();
    await user.click(await screen.findByRole('button',{name:'Breakdown'}));
    await user.click(await screen.findByRole('button',{name:'Show payroll details for 2026-04-28'}));
    expect(await screen.findByText('12 hours @ £12.345')).toBeTruthy();
    expect(screen.getByText('3 hours @ £16.46')).toBeTruthy();
    expect(screen.queryByText('8 hours @ £12.345')).toBeNull();
    expect(screen.queryByText('4 hours @ £12.345')).toBeNull();
    expect(screen.getByText('£148.14')).toBeTruthy();
    expect(lines[0]!.label).toBe('8 hours @ £12.345');
    expect(lines[0]!.forecast_pence).toBe(9876);
  });
});
