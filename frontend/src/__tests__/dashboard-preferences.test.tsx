import { Component, type ReactNode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Dashboard } from "../pages/Dashboard";
import { setHouseholdId, setProfileId } from "../api/client";

vi.mock("../api/useReview", () => ({ useReview: () => ({ total: 0 }) }));
vi.mock("../widgets/registry", () => {
  const widgets = ["accounts", "upcoming"].map(id => ({ id,
    title: id === "accounts" ? "Accounts" : "Upcoming", span: 1,
    Component: () => <h2>{id === "accounts" ? "Accounts" : "Upcoming"}</h2> }));
  widgets.push({ id: "system", title: "System status", span: 1,
    roles: ["admin"], Component: () => <h2>System status</h2> } as typeof widgets[number]);
  return { DEFAULT_ORDER: ["accounts", "upcoming", "system"], WIDGETS: widgets,
    widgetById: (id: string) => widgets.find(w => w.id === id) };
});

it.each(["member", "viewer", "admin"])("retained drafts wait for %s role before mounting restricted widgets", async role => {
  setHouseholdId(1); setProfileId(1);
  sessionStorage.setItem("ff.dashboard.draft.1:1", JSON.stringify({
    order: ["system", "accounts", "upcoming"], hidden: [],
  }));
  let complete!: (response: Response) => void;
  vi.spyOn(globalThis, "fetch").mockImplementation(async input => {
    if (String(input).endsWith("/profiles")) return profiles();
    if (String(input).includes("/profiles/me/dashboard")) {
      return new Promise<Response>(resolve => { complete = resolve; });
    }
    return json({ items: [{ id: 1, name: "Synthetic household" }] });
  });
  mount();
  await waitFor(() => expect(complete).toBeTypeOf("function"));
  expect(screen.queryByRole("heading", { name: "System status" })).toBeNull();
  await act(async () => { complete(json({ role, layout: initial })); });
  if (role === "admin") await screen.findByRole("heading", { name: "System status" });
  else expect(screen.queryByRole("heading", { name: "System status" })).toBeNull();
});

class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <p>Dashboard crashed</p> : this.props.children; }
}
const initial = { order: ["accounts", "upcoming"], hidden: [] as string[] };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
const profiles = () => json({ items: [1, 2].map(id => ({ id,
  display_name: "Synthetic", role: "member", is_active: true })) });
function fixture(layout: unknown = initial, role: string | null = "member") {
  let saved = layout;
  let fail = false;
  const writes: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input).endsWith("/profiles")) return role === null ? json({ items: [] }) : profiles();
    if (String(input).includes("/profiles/me/dashboard")) {
      if (init?.method === "PUT") {
        writes.push(JSON.parse(String(init.body)));
        if (fail) return json({ title: "Save unavailable" }, 503);
        saved = JSON.parse(String(init.body));
      }
      return json({ role, layout: saved });
    }
    return json({ items: [{ id: 1, name: "Synthetic household" }] });
  });
  return { writes, reject: () => { fail = true; }, recover: () => { fail = false; } };
}
function mount() { return render(<MemoryRouter><Boundary><Dashboard /></Boundary></MemoryRouter>); }
async function customise() {
  await screen.findByRole("heading", { name: "Accounts", level: 2 });
  fireEvent.click(screen.getByRole("button", { name: "Customise" }));
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); sessionStorage.clear(); });

for (const value of [null, {}, { order: [], hidden: null }, { widgets: ["old-widget"] }, 3]) {
  it(`recovers malformed local layout ${JSON.stringify(value)} in the component`, async () => {
    localStorage.setItem("ff.dashboard", JSON.stringify(value));
    const api = fixture(null, null);
    mount();
    await screen.findByRole("heading", { name: "Accounts", level: 2 });
    expect(screen.queryByText("Dashboard crashed")).toBeNull();
    expect(api.writes).toEqual([]);
  });
}
it("normalizes malformed remote arrays and preserves valid hidden intent", async () => {
  fixture({ order: {}, hidden: ["accounts", "upcoming", "unknown", 4] });
  mount();
  await waitFor(() => expect(screen.queryByText("Dashboard crashed")).toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "Customise" }));
  await screen.findByRole("button", { name: "Show Accounts" });
  expect(document.querySelectorAll("[data-widget]")).toHaveLength(0);
});
it("retains rejected draft through remount, then retries and persists", async () => {
  const api = fixture(); api.reject();
  const view = mount(); await customise();
  fireEvent.click(screen.getByRole("button", { name: "Hide Accounts" }));
  await screen.findByText(/Changes not saved/);
  expect(screen.getByRole("button", { name: "Show Accounts" })).toBeTruthy();
  view.unmount(); mount();
  await screen.findByText(/Changes not saved/);
  expect(screen.queryByRole("heading", { name: "Accounts", level: 2 })).toBeNull();
  api.recover();
  await waitFor(() => expect(screen.getByRole("button", { name: "Retry saving layout" }).hasAttribute("disabled")).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Retry saving layout" }));
  await screen.findByText("Layout saved to your profile.");
  cleanup(); mount();
  await waitFor(() => expect(screen.queryByRole("heading", { name: "Accounts", level: 2 })).toBeNull());
  expect(api.writes).toHaveLength(2);
});
it("reverts rejected layout to the confirmed profile layout", async () => {
  const api = fixture(); api.reject(); mount(); await customise();
  fireEvent.click(screen.getByRole("button", { name: "Hide Accounts" }));
  await screen.findByText(/Changes not saved/);
  fireEvent.click(screen.getByRole("button", { name: "Revert layout" }));
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await screen.findByRole("heading", { name: "Accounts", level: 2 });
  expect(screen.queryByText(/Changes not saved/)).toBeNull();
  expect(api.writes).toHaveLength(1);
});
it("serializes pending UI changes across rapid clicks and route remount", async () => {
  let finish!: (response: Response) => void;
  const writes: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input).endsWith("/profiles")) return profiles();
    if (String(input).includes("/profiles/me/dashboard")) {
      if (init?.method === "PUT") {
        writes.push(JSON.parse(String(init.body)));
        return new Promise<Response>(resolve => { finish = resolve; });
      }
      return json({ role: "member", layout: initial });
    }
    return json({ items: [{ id: 1, name: "Synthetic" }] });
  });
  const view = mount(); await customise();
  fireEvent.click(screen.getByRole("button", { name: "Hide Accounts" }));
  fireEvent.click(screen.getByRole("button", { name: "Hide Upcoming" }));
  expect(writes).toHaveLength(1);
  view.unmount(); mount();
  await screen.findByText("Saving layout…");
  fireEvent.click(screen.getByRole("button", { name: "Customise" }));
  expect(screen.getByRole("button", { name: "Hide Upcoming" }).hasAttribute("disabled")).toBe(true);
  await act(async () => { finish(json({ role: "member", layout: { ...initial, hidden: ["accounts"] } })); });
  await screen.findByText("Layout saved to your profile.");
  expect(writes).toHaveLength(1);
});
it("does not inherit a rejected draft after profile or household changes", async () => {
  setHouseholdId(1); setProfileId(1);
  const api = fixture(); api.reject(); const view = mount(); await customise();
  fireEvent.click(screen.getByRole("button", { name: "Hide Accounts" }));
  await screen.findByText(/Changes not saved/);
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  setProfileId(2); view.rerender(<MemoryRouter><Boundary><Dashboard /></Boundary></MemoryRouter>);
  await screen.findByRole("heading", { name: "Accounts", level: 2 });
  expect(screen.queryByText(/Changes not saved/)).toBeNull();
  setHouseholdId(2); view.rerender(<MemoryRouter><Boundary><Dashboard /></Boundary></MemoryRouter>);
  await screen.findByRole("heading", { name: "Accounts", level: 2 });
  expect(screen.queryByText(/Changes not saved/)).toBeNull();
});

it("keeps valid local order and deliberately hidden widgets without saving defaults", async () => {
  localStorage.setItem("ff.dashboard", JSON.stringify({ order: ["upcoming", "accounts", "unknown", "upcoming"],
    hidden: ["accounts"] }));
  const api = fixture(null, null); mount();
  await screen.findByRole("heading", { name: "Upcoming", level: 2 });
  expect(screen.queryByRole("heading", { name: "Accounts", level: 2 })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Customise" }));
  expect(screen.getAllByRole("listitem")[0]!.textContent).toContain("Upcoming");
  expect(screen.getByRole("button", { name: "Show Accounts" })).toBeTruthy();
  expect(api.writes).toEqual([]);
});

it("ignores a late successful save from another profile and restores the right draft", async () => {
  setHouseholdId(1); setProfileId(1);
  let finish!: (response: Response) => void;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input).endsWith("/profiles")) return profiles();
    if (String(input).includes("/profiles/me/dashboard")) {
      if (init?.method === "PUT") return new Promise<Response>(resolve => { finish = resolve; });
      return json({ role: "member", layout: initial });
    }
    return json({ items: [{ id: 1, name: "Synthetic" }] });
  });
  const view = mount(); await customise();
  fireEvent.click(screen.getByRole("button", { name: "Hide Accounts" }));
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  setProfileId(2); view.rerender(<MemoryRouter><Boundary><Dashboard /></Boundary></MemoryRouter>);
  await screen.findByRole("heading", { name: "Accounts", level: 2 });
  await act(async () => { finish(json({ role: "member", layout: { ...initial, hidden: ["accounts"] } })); });
  expect(screen.getByRole("heading", { name: "Accounts", level: 2 })).toBeTruthy();
  expect(screen.queryByText("Layout saved to your profile.")).toBeNull();
  expect(sessionStorage.length).toBe(0);
});

it("a late GET on remount cannot replace a successfully saved draft", async () => {
  let put!: (response: Response) => void;
  let get!: (response: Response) => void;
  let gets = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input).endsWith("/profiles")) return profiles();
    if (String(input).includes("/profiles/me/dashboard")) {
      if (init?.method === "PUT") return new Promise<Response>(resolve => { put = resolve; });
      if (++gets > 1) return new Promise<Response>(resolve => { get = resolve; });
      return json({ role: "member", layout: initial });
    }
    return json({ items: [{ id: 1, name: "Synthetic" }] });
  });
  const view = mount(); await customise();
  fireEvent.click(screen.getByRole("button", { name: "Hide Accounts" }));
  view.unmount(); mount();
  await waitFor(() => expect(gets).toBe(2));
  await act(async () => { put(json({ role: "member", layout: { ...initial, hidden: ["accounts"] } })); });
  await screen.findByText("Layout saved to your profile.");
  await act(async () => { get(json({ role: "member", layout: initial })); });
  expect(screen.queryByRole("heading", { name: "Accounts", level: 2 })).toBeNull();
  expect(screen.getByRole("heading", { name: "Upcoming", level: 2 })).toBeTruthy();
});

it("keeps a 403 save rejection explicit and enables Revert after delayed profile load", async () => {
  let completeGet!: (response: Response) => void;
  let calls = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input).endsWith("/profiles")) return profiles();
    if (String(input).includes("/profiles/me/dashboard")) {
      if (init?.method === "PUT") { calls++; return json({ title: "Forbidden" }, 403); }
      return new Promise<Response>(resolve => { completeGet = resolve; });
    }
    return json({ items: [{ id: 1, name: "Synthetic" }] });
  });
  mount();
  await waitFor(() => expect(completeGet).toBeTypeOf("function"));
  await act(async () => { completeGet(json({ role: "viewer", layout: initial })); });
  await customise(); fireEvent.click(screen.getByRole("button", { name: "Hide Accounts" }));
  await screen.findByText(/403 Forbidden/);
  expect(calls).toBe(1);
  expect(screen.getByRole("button", { name: "Revert layout" }).hasAttribute("disabled")).toBe(false);
});

it("waits for standalone identity before fetching or saving profile preferences", async () => {
  let finish!: (response: Response) => void;
  let resolved = false;
  const selected = { items: [{ id: 2, display_name: "Synthetic", role: "member", is_active: true }] };
  let households = 0;
  const dashboardHeaders: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input).endsWith("/profiles")) return resolved ? json(selected)
      : new Promise<Response>(resolve => { finish = resolve; });
    if (String(input).includes("/profiles/me/dashboard")) {
      dashboardHeaders.push(init?.headers);
      return json({ role: "member", layout: initial });
    }
    households++;
    return json({ items: [{ id: 1, name: "Synthetic" }] });
  });
  mount();
  await waitFor(() => expect(households).toBeGreaterThan(0));
  await waitFor(() => expect(finish).toBeTypeOf("function"));
  expect(dashboardHeaders).toEqual([]);
  await act(async () => { resolved = true; finish(json(selected)); });
  await screen.findByRole("heading", { name: "Accounts", level: 2 });
  expect(dashboardHeaders).toEqual([expect.objectContaining({ "X-Profile-Id": "2" })]);
});

it("a failed preference read shows fallback without permitting an accidental overwrite", async () => {
  let unavailable = true;
  const writes: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input).endsWith("/profiles")) return profiles();
    if (String(input).includes("/profiles/me/dashboard")) {
      if (init?.method === "PUT") writes.push(init.body);
      return unavailable ? json({ title: "Read forbidden" }, 403) : json({ role: "member", layout: initial });
    }
    return json({ items: [{ id: 1, name: "Synthetic" }] });
  });
  mount(); await screen.findByText(/403 Read forbidden/); await customise();
  const hide = screen.getByRole("button", { name: "Hide Accounts" });
  expect(hide.hasAttribute("disabled")).toBe(true); fireEvent.click(hide);
  expect(writes).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  unavailable = false; fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(screen.queryByText(/403 Read forbidden/)).toBeNull());
  await customise();
  expect(screen.getByRole("button", { name: "Hide Accounts" }).hasAttribute("disabled")).toBe(false);
});

it("blocked tab storage cannot crash Dashboard or falsely promise draft retention", async () => {
  vi.spyOn(window, "sessionStorage", "get").mockImplementation(() => { throw new DOMException("Storage unavailable", "SecurityError"); });
  const api = fixture(); api.reject(); mount(); await customise();
  fireEvent.click(screen.getByRole("button", { name: "Hide Accounts" }));
  await screen.findByText(/503 Save unavailable.*could not retain your draft/);
  expect(screen.queryByText("Dashboard crashed")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Revert layout" }));
  expect(screen.queryByText(/Changes not saved/)).toBeNull();
});
