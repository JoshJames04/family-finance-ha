import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Overtime } from "../pages/Overtime";

const EMP = { id: 1, household_id: 1, profile_id: 1, employer: "Weekly proof",
  jurisdiction: "england_wales", contracted_hours: "37.50", overtime_multipliers: "{}" };
const row = (id: number, status = "draft", date = "2026-11-02") => ({
  id, employment_id: 1, work_date: date, hours: "1.25", multiplier_bps: 15000,
  day_type: "weekday", status, hourly_rate_pence: 1000, pay_pence: 1875,
  notes: null, revision: String(id).padStart(64, "0"),
});
type Row = ReturnType<typeof row>;
type Call = { url: string; method: string; body: unknown };

function stub(initial: Row[], failStatus = 0, beforeWrite?: () => Promise<void>) {
  let rows = initial;
  const calls: Call[] = [];
  let afterWrite: (() => void) | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input), method = init?.method ?? "GET";
    const payload = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body: payload });
    if (method === "POST" && url.includes("/overtime-weeks/")) {
      await beforeWrite?.();
      if (failStatus) return new Response(JSON.stringify({ detail: "The week changed; refresh before trying again" }), {
        status: failStatus, headers: { "Content-Type": "application/json" },
      });
      const action = url.split("/").at(-1);
      rows = rows.map(r => payload.entries.some((e: {id: number}) => e.id === r.id)
        ? { ...r, status: action === "submit" ? "submitted" : action === "approve" ? "approved" : "rejected" } : r);
      afterWrite?.();
      return Response.json({ week_start: "2026-11-02", week_end: "2026-11-08", action,
        updated_count: payload.entries.length });
    }
    if (method === "PATCH") {
      const id = Number(url.split("/").at(-1));
      rows = rows.map(r => r.id === id ? { ...r, ...payload, status: "draft" } : r);
      return Response.json(rows.find(r => r.id === id));
    }
    if (url.includes("/overtime-entries")) return Response.json(rows);
    if (url.includes("/households")) return Response.json({ items: [{ id: 1, name: "Proof" }], total: 1 });
    if (url.includes("/employments")) return Response.json({ items: [EMP], total: 1 });
    return Response.json({ items: [], total: 0 });
  });
  return { calls, add: (entry: Row) => { rows = [...rows, entry]; },
    afterWrite: (fn: () => void) => { afterWrite = fn; } };
}
const show = () => render(<MemoryRouter><Overtime /></MemoryRouter>);
const writes = (calls: Call[]) => calls.filter(c => c.method === "POST");

describe("weekly overtime workflow", () => {
  afterEach(cleanup);

  it("groups historic entries across ISO years without making writes", async () => {
    const { calls } = stub([row(3, "approved", "2026-01-05"), row(2, "submitted", "2026-01-04"),
      row(1, "draft", "2025-12-29")]);
    show();
    const first = await screen.findByRole("region", { name: "Week 1, 2026" });
    expect(first.textContent).toContain("29 Dec 2025");
    expect(first.textContent).toContain("4 Jan 2026");
    expect(first.textContent).toContain("2.5h");
    expect(first.textContent).toContain("£37.50");
    expect(screen.getAllByRole("heading", { level: 2 }).map(e => e.textContent))
      .toEqual(["Week 2, 2026", "Week 1, 2026"]);
    expect(within(first).getAllByRole("row")).toHaveLength(3);
    expect(writes(calls)).toHaveLength(0);
  });

  it("confirms and submits only the complete eligible draft snapshot", async () => {
    const a = row(1), b = row(2, "draft", "2026-11-08");
    const { calls } = stub([a, b, row(3, "approved"), row(4, "rejected")]);
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Submit week" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("2 entries");
    expect(dialog.textContent).toContain("2.5h");
    expect(dialog.textContent).toContain("£37.50");
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Submit 2 entries" }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0]!.url).toContain("/employments/1/overtime-weeks/2026-11-02/submit");
    expect(writes(calls)[0]!.body).toEqual({ entries: [
      { id: a.id, revision: a.revision }, { id: b.id, revision: b.revision },
    ] });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(screen.getByRole("button", { name: "Submit week" }).hasAttribute("disabled")).toBe(true));
  });

  it.each(["Approve", "Reject"])("%s week affects submitted entries only", async verb => {
    const submitted = row(2, "submitted");
    const { calls } = stub([row(1), submitted, row(3, "approved")]);
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: `${verb} week` }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: `${verb} 1 entry` }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0]!.url).toContain(`/${verb.toLowerCase()}`);
    expect(writes(calls)[0]!.body).toEqual({ entries: [{ id: submitted.id, revision: submitted.revision }] });
  });

  it("cancels without changing any entries", async () => {
    const { calls } = stub([row(1)]);
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Submit week" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(writes(calls)).toHaveLength(0);
  });

  it("requires a refresh after a stale-snapshot conflict, without silently retrying", async () => {
    const { calls } = stub([row(1)], 409);
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Submit week" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Submit 1 entry" }));
    expect((await within(dialog).findByRole("alert")).textContent).toMatch(/week changed/i);
    expect(within(dialog).getByRole("button", { name: "Submit 1 entry" }).hasAttribute("disabled")).toBe(true);
    expect(writes(calls)).toHaveLength(1);
    const reads = calls.length;
    await user.click(within(dialog).getByRole("button", { name: "Refresh week" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(calls.length).toBeGreaterThan(reads));
    expect(writes(calls)).toHaveLength(1);
  });

  it("leaves a newly added draft eligible after an earlier weekly submission", async () => {
    const api = stub([row(1)]);
    api.afterWrite(() => api.add(row(2, "draft", "2026-11-08")));
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Submit week" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Submit 1 entry" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const submit = await screen.findByRole("button", { name: "Submit week" });
    expect(submit.hasAttribute("disabled")).toBe(false);
    await user.click(submit);
    expect(within(await screen.findByRole("dialog")).getByRole("button", { name: "Submit 1 entry" })).toBeTruthy();
  });

  it("prevents duplicate clicks and dismissal while a weekly request is pending", async () => {
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    const { calls } = stub([row(1)], 0, () => gate);
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Submit week" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Submit 1 entry" }));
    const pending = within(dialog).getByRole("button", { name: "Saving…" });
    expect(pending.hasAttribute("disabled")).toBe(true);
    expect(within(dialog).getByRole("button", { name: "Cancel" }).hasAttribute("disabled")).toBe(true);
    fireEvent.click(pending);
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(writes(calls)).toHaveLength(1);
    release();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("shows a failed request and permits an explicit retry without false success", async () => {
    const { calls } = stub([row(1)], 500);
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Submit week" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Submit 1 entry" }));
    expect(await within(dialog).findByRole("alert")).toBeTruthy();
    const retry = within(dialog).getByRole("button", { name: "Submit 1 entry" });
    expect(retry.hasAttribute("disabled")).toBe(false);
    expect(screen.queryByRole("button", { name: "Refresh week" })).toBeNull();
    expect(writes(calls)).toHaveLength(1);
    await user.click(retry);
    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("moves an edited work date into the correct weekly group", async () => {
    stub([row(1)]);
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Edit entry/ }));
    fireEvent.change(await screen.findByLabelText(/Date worked/), { target: { value: "2026-11-09" } });
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("region", { name: "Week 46, 2026" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Week 45, 2026" })).toBeNull();
  });
});
