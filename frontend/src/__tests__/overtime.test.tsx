/** Overtime page: entry list with status-driven actions, the new-entry
 * form posts date+hours (day type auto), edit uses PATCH, and the
 * submit/approve/reject/rework transitions hit the action endpoint. */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Overtime } from "../pages/Overtime";

const EMP = {
  id: 1, household_id: 1, profile_id: 1, employer: "Acme",
  jurisdiction: "england_wales", ni_category: "A",
  student_loan_plan: "none", pension_scheme_ref: null,
  contracted_hours: "37.50", payday_rule: null,
  pension_mode: "none", pension_employee_bps: 0,
  pension_fixed_pence: null, work_days_per_week: 5,
  overtime_multipliers: "{}",
};

const DRAFT = {
  id: 9, employment_id: 1, work_date: "2026-11-07", hours: "4.00",
  multiplier_bps: 15000, day_type: "saturday", status: "draft",
  hourly_rate_pence: 1981, pay_pence: 11886, notes: null,
};

const SUBMITTED = { ...DRAFT, id: 10, status: "submitted" };
const REJECTED = { ...DRAFT, id: 11, status: "rejected" };
const APPROVED = { ...DRAFT, id: 12, status: "approved" };

function stubFetch(
  calls: { url: string; method: string; body: string }[],
  entries: object[] = [],
) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: String(init?.body ?? "") });
      const body = method !== "GET"
        ? { ok: true }
        : url.includes("/api/v1/households")
          ? { items: [{ id: 1, name: "Home" }], total: 1 }
          : url.includes("/overtime-entries")
            ? entries
            : url.includes("/api/v1/employments")
              ? { items: [EMP], total: 1 }
              : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

describe("Overtime", () => {
  afterEach(cleanup);

  it("lists entries with status-driven actions", async () => {
    stubFetch([], [DRAFT, SUBMITTED, REJECTED, APPROVED]);
    render(<MemoryRouter><Overtime /></MemoryRouter>);
    expect(await screen.findAllByText("draft")).toHaveLength(1);
    expect(await screen.findAllByText("submitted")).toHaveLength(1);
    expect(await screen.findAllByText("rejected")).toHaveLength(1);
    expect(await screen.findAllByText("approved")).toHaveLength(1);
    // draft rows can be edited/submitted/deleted; submitted rows offer
    // approve/reject; rejected offer rework; approved are locked.
    expect(screen.getByRole("button", { name: "Edit entry" }))
      .toBeTruthy();
    expect(screen.getByRole("button", { name: "Submit" }))
      .toBeTruthy();
    expect(screen.getByRole("button", { name: "Approve" }))
      .toBeTruthy();
    expect(screen.getByRole("button", { name: "Reject" }))
      .toBeTruthy();
    expect(screen.getByRole("button", { name: "Rework" }))
      .toBeTruthy();
    expect(screen.getAllByLabelText("Delete entry")).toHaveLength(2);
  });

  it("posts a new entry with date+hours only (day type auto)", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls, []);
    const user = userEvent.setup();
    render(<MemoryRouter><Overtime /></MemoryRouter>);
    await user.click(await screen.findByRole("button",
      { name: /New entry/ }));
    fireEvent.change(await screen.findByLabelText(/Date worked/),
      { target: { value: "2026-11-08" } });
    await user.type(screen.getByLabelText(/Hours/), "4");
    await user.click(screen.getByRole("button", { name: "Log overtime" }));
    await waitFor(() => {
      const post = calls.find((c) => c.method === "POST"
        && c.url.includes("/overtime-entries"));
      expect(post).toBeDefined();
      const body = JSON.parse(post!.body);
      expect(body.work_date).toBe("2026-11-08");
      expect(body.hours).toBe("4");
      expect(body.day_type).toBeUndefined();
    });
  });

  it("PATCHes an edit and posts transitions to the action path",
    async () => {
      const calls: { url: string; method: string; body: string }[] = [];
      stubFetch(calls, [DRAFT, SUBMITTED]);
      const user = userEvent.setup();
      render(<MemoryRouter><Overtime /></MemoryRouter>);
      await user.click(await screen.findByRole("button",
        { name: "Edit entry" }));
      const hours = await screen.findByLabelText(/Hours/);
      await user.clear(hours);
      await user.type(hours, "6");
      await user.click(screen.getByRole("button",
        { name: "Save changes" }));
      await waitFor(() => {
        const patch = calls.find((c) => c.method === "PATCH");
        expect(patch).toBeDefined();
        expect(patch!.url).toContain("/overtime-entries/9");
        expect(JSON.parse(patch!.body).hours).toBe("6");
      });
      // the list remounts after save — wait for the row actions to
      // reappear before driving the transition.
      await user.click(await screen.findByRole("button",
        { name: "Approve" }));
      await waitFor(() => {
        const post = calls.find((c) => c.method === "POST"
          && c.url.includes("/overtime-entries/10/approve"));
        expect(post).toBeDefined();
      });
    });
});
