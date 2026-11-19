import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Dashboard } from "../pages/Dashboard";
import { WIDGETS } from "../widgets/registry";

const state = vi.hoisted(() => ({ ingress: false }));
vi.mock("../ingress", async (original) => ({
  ...await original<typeof import("../ingress")>(),
  isIngress: () => state.ingress,
}));

const accounts = [
  { id: 1, name: "Other member account", archived: false, owner_profile_id: 20 },
  { id: 2, name: "My salary account", archived: false, owner_profile_id: 10 },
  { id: 3, name: "Shared account", archived: false, owner_profile_id: null },
];
const job = { id: 1, profile_id: 10, paid_into_account_id: 2,
  is_active: true, start_date: null, end_date: null };
const profiles = [{ id: 10, display_name: "Member A", role: "admin", is_active: true },
  { id: 20, display_name: "Member B", role: "member", is_active: true }];
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});

function setup(jobs = [job], options: {
  employmentRead?: (url: string) => Promise<Response>; accountRows?: typeof accounts; visible?: string[];
} = {}) {
  const projections: number[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    const projected = url.match(/\/accounts\/(\d+)\/projection/);
    if (projected) {
      const id = Number(projected[1]);
      projections.push(id);
      return response({ account_id: id, start_balance_pence: 10000,
        next_payday: null, safe_to_spend_pence: 10000,
        committed_until_payday_pence: 0, min_balance_pence: 10000,
        min_balance_date: null, threshold_pence: 0, below_threshold: false,
        days: [{ date: "2026-12-01", balance_pence: 10000 }] });
    }
    if (url.includes("/profiles/me/dashboard")) return response({ role: "admin", layout: {
      order: ["safe-to-spend", "forecast"],
      hidden: WIDGETS.map(w => w.id).filter(id => !(options.visible ?? ["safe-to-spend", "forecast"]).includes(id)),
    } });
    if (url.includes("/auth/me")) return response(profiles[0]);
    if (url.includes("/profiles")) return response({ items: profiles, total: 2 });
    if (url.includes("/households")) return response({ items: [{ id: 1, name: "Synthetic" }], total: 1 });
    if (url.includes("/employments")) return options.employmentRead?.(url)
      ?? response({ items: jobs, total: jobs.length });
    if (url.includes("/accounts")) return response({ items: options.accountRows ?? accounts, total: 3 });
    return response({ items: [], total: 0 });
  });
  return projections;
}
function dashboard() { return render(<MemoryRouter><Dashboard /></MemoryRouter>); }
const selector = () => screen.getByRole("combobox", { name: "Safe to spend account" }) as HTMLSelectElement;

beforeEach(() => {
  localStorage.clear(); sessionStorage.clear();
  localStorage.setItem("ff.householdId", "1");
  localStorage.setItem("ff.profileId", "10");
  state.ingress = false;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); sessionStorage.clear(); });

it("defaults both Overview widgets to my salary account rather than the first visible account", async () => {
  const requested = setup([job, { ...job, id: 2, profile_id: 20, paid_into_account_id: 1 }]);
  dashboard();
  await waitFor(() => expect(selector().value).toBe("2"));
  await waitFor(() => expect(requested.length).toBeGreaterThan(0));
  expect(new Set(requested)).toEqual(new Set([2]));
  expect(await within(document.querySelector('[data-widget="forecast"]')!).findByText(/My salary account · Lowest/)).toBeTruthy();
});

it("uses authenticated HA identity instead of a stale stored member", async () => {
  state.ingress = true;
  localStorage.setItem("ff.profileId", "20");
  const requested = setup([job, { ...job, id: 2, profile_id: 20, paid_into_account_id: 1 }]);
  dashboard();
  await waitFor(() => expect(selector().value).toBe("2"));
  await waitFor(() => expect(requested.length).toBeGreaterThan(0));
  expect(new Set(requested)).toEqual(new Set([2]));
});

it.each([
  ["no jobs", []],
  ["distinct salary accounts", [job, { ...job, id: 2, paid_into_account_id: 3 }]],
  ["another member's job", [{ ...job, profile_id: 20 }]],
  ["future job", [{ ...job, start_date: "2999-01-01" }]],
  ["ended job", [{ ...job, end_date: "2000-01-01" }]],
  ["inactive job", [{ ...job, is_active: false }]],
  ["inaccessible destination", [{ ...job, paid_into_account_id: 999 }]],
])("asks for an account with %s and never guesses a projection", async (_name, jobs) => {
  const requested = setup(jobs as typeof job[]);
  dashboard();
  expect(await screen.findByText("Choose an account to see what's safe to spend.")).toBeTruthy();
  expect(selector().value).toBe("");
  expect(requested).toEqual([]);
});

it("ignores archived salary accounts", async () => {
  const requested = setup([job], { accountRows: accounts.map(a => ({ ...a, archived: a.id === 2 })) });
  dashboard();
  await screen.findByText("Choose an account to see what's safe to spend.");
  expect(requested).toEqual([]);
});

it("deduplicates several current jobs paid into the same account", async () => {
  const requested = setup([job, { ...job, id: 2 }]);
  dashboard();
  await waitFor(() => expect(selector().value).toBe("2"));
  await waitFor(() => expect(requested.length).toBeGreaterThan(0));
  expect(new Set(requested)).toEqual(new Set([2]));
});

it("waits for salary setup without briefly projecting the first account", async () => {
  let finish!: (value: Response) => void;
  const pending = new Promise<Response>(resolve => { finish = resolve; });
  let asked = false;
  const requested = setup([job], { employmentRead: () => { asked = true; return pending; } });
  dashboard();
  await waitFor(() => expect(asked).toBe(true));
  expect(requested).toEqual([]);
  finish(response({ items: [job], total: 1 }));
  await waitFor(() => expect(selector().value).toBe("2"));
  await waitFor(() => expect(requested.length).toBeGreaterThan(0));
  expect(new Set(requested)).toEqual(new Set([2]));
});

it("surfaces an employment read failure without treating it as an empty configuration", async () => {
  const requested = setup([job], { employmentRead: async () => response({ detail: "Payroll lookup unavailable" }, 400) });
  dashboard();
  await screen.findAllByText(/Payroll lookup unavailable/);
  expect(requested).toEqual([]);
});

it("switches both widgets together and restores the salary default on a new visit", async () => {
  setup();
  const first = dashboard();
  await waitFor(() => expect(selector().value).toBe("2"));
  await userEvent.setup().selectOptions(selector(), "3");
  await waitFor(() => expect(within(document.querySelector('[data-widget="forecast"]')!).getByText(/Shared account · Lowest/)).toBeTruthy());
  first.unmount();
  dashboard();
  await waitFor(() => expect(selector().value).toBe("2"));
});

it("does not revive a previous member's manual selection after switching members twice", async () => {
  setup([job, { ...job, id: 2, profile_id: 20, paid_into_account_id: 1 }]);
  const view = dashboard();
  await waitFor(() => expect(selector().value).toBe("2"));
  await userEvent.setup().selectOptions(selector(), "3");
  localStorage.setItem("ff.profileId", "20");
  view.rerender(<MemoryRouter><Dashboard /></MemoryRouter>);
  await waitFor(() => expect(selector().value).toBe("1"));
  localStorage.setItem("ff.profileId", "10");
  view.rerender(<MemoryRouter><Dashboard /></MemoryRouter>);
  await waitFor(() => expect(selector().value).toBe("2"));
});

it("allows an explicit account choice with only the forecast widget visible", async () => {
  setup([], { visible: ["forecast"] });
  dashboard();
  const forecastAccount = await screen.findByRole("combobox", { name: "Forecast account" });
  await waitFor(() => expect((forecastAccount as HTMLSelectElement).disabled).toBe(false));
  await userEvent.setup().selectOptions(forecastAccount, "3");
  expect(await within(document.querySelector('[data-widget="forecast"]')!).findByText(/Shared account · Lowest/)).toBeTruthy();
});

it("finds a current salary account after a full page of historical jobs", async () => {
  const history = Array.from({ length: 200 }, (_, id) => ({ ...job, id, is_active: false }));
  const requested = setup([job], { employmentRead: async url => {
    const page = Number(new URL(url, "http://localhost").searchParams.get("page") ?? 1);
    return response({ items: page === 1 ? history : [job], total: 201, page, size: 200 });
  } });
  dashboard();
  await waitFor(() => expect(selector().value).toBe("2"));
  await waitFor(() => expect(requested.length).toBeGreaterThan(0));
  expect(new Set(requested)).toEqual(new Set([2]));
});

it("does not assume a unique salary account before the last employment page arrives", async () => {
  const history = Array.from({ length: 199 }, (_, id) => ({ ...job, id: id + 2, is_active: false }));
  let finish!: (value: Response) => void;
  const secondPage = new Promise<Response>(resolve => { finish = resolve; });
  let secondAsked = false;
  const requested = setup([job], { employmentRead: async url => {
    if (new URL(url, "http://localhost").searchParams.get("page") === "2") {
      secondAsked = true; return secondPage;
    }
    return response({ items: [job, ...history], total: 201, page: 1, size: 200 });
  } });
  dashboard();
  await waitFor(() => expect(secondAsked).toBe(true));
  expect(requested).toEqual([]);
  finish(response({ items: [{ ...job, id: 202, paid_into_account_id: 3 }], total: 201, page: 2, size: 200 }));
  await screen.findByText("Choose an account to see what's safe to spend.");
  expect(requested).toEqual([]);
});

it("fails visibly rather than choosing from an incomplete employment result", async () => {
  const requested = setup([job], { employmentRead: async url => {
    const page = Number(new URL(url, "http://localhost").searchParams.get("page") ?? 1);
    return response({ items: page === 1 ? [job] : [], total: 2, page, size: 200 });
  } });
  dashboard();
  await screen.findAllByText(/Employment list is incomplete/, {}, { timeout: 8000 });
  expect(requested).toEqual([]);
}, 10000);

it("retries a failed salary lookup and selects the recovered account", async () => {
  let failed = true;
  setup([job], { employmentRead: async () => failed
    ? response({ detail: "Payroll lookup unavailable" }, 400)
    : response({ items: [job], total: 1 }) });
  dashboard();
  await screen.findAllByText(/Payroll lookup unavailable/);
  failed = false;
  await userEvent.setup().click(screen.getAllByRole("button", { name: "Retry" })[0]!);
  await waitFor(() => expect(selector().value).toBe("2"));
  await screen.findByText(/My salary account · Lowest/);
});

it("rejects missing pagination metadata without using a partial salary list", async () => {
  const requested = setup([job], { employmentRead: async url => {
    const page = Number(new URL(url, "http://localhost").searchParams.get("page") ?? 1);
    return response(page === 1 ? { items: [job] } : { items: [], total: 0 });
  } });
  dashboard();
  await screen.findAllByText(/Employment list is incomplete/, {}, { timeout: 8000 });
  expect(requested).toEqual([]);
}, 10000);

it("does not read payroll when both projection widgets are hidden", async () => {
  setup([job], { visible: ["accounts"] });
  dashboard();
  await screen.findByRole("heading", { name: "Accounts" });
  expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/employments"))).toBe(false);
});
