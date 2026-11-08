/** #274: payable objects — list, section grouping, create, pause/resume,
 * post-next materialise. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Bills } from "../pages/Bills";

const COMMITMENTS = {
  items: [
    { id: 1, name: "Broadband", account_id: 1, category_id: 2,
      amount_pence: -2800, formula: null, frequency: "monthly",
      day_of_month: 14, next_date: "2026-05-14", last_generated: null,
      end_date: null, occurrence_count: null, generated_count: 3,
      active: true, notes: null },
    { id: 2, name: "Gym", account_id: 1, category_id: 3,
      amount_pence: -2999, formula: null, frequency: "monthly",
      day_of_month: 1, next_date: "2026-06-01", last_generated: null,
      end_date: null, occurrence_count: null, generated_count: 0,
      active: false, notes: null },
  ],
  total: 2, page: 1, size: 200,
};

const CATEGORIES = {
  items: [
    { id: 1, parent_id: null, name: "Household Bills", is_system: false, archived: false },
    { id: 2, parent_id: 1, name: "Utilities", is_system: false, archived: false },
    { id: 3, parent_id: null, name: "Personal", is_system: false, archived: false },
  ],
  total: 3, page: 1, size: 200,
};

function stubFetch(calls: { url: string; method: string; body: string }[]) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: String(init?.body ?? "") });
      const body = method !== "GET"
        ? COMMITMENTS.items[0]
        : url.includes("/api/v1/households")
          ? { items: [{ id: 1, name: "Home" }], total: 1 }
          : url.includes("/api/v1/commitments")
            ? COMMITMENTS
            : url.includes("/api/v1/categories")
              ? CATEGORIES
              : url.includes("/api/v1/accounts")
                ? { items: [{ id: 1, name: "Joint", type: "joint",
                    currency: "GBP", opening_balance_pence: 0,
                    opening_date: "2026-01-01", owner_profile_id: null,
                    institution_ref: null, archived: false,
                    min_balance_pence: null }], total: 1 }
                : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

describe("Bills", () => {
  afterEach(cleanup);

  it("lists commitments grouped by budget section", async () => {
    stubFetch([]);
    render(<MemoryRouter><Bills /></MemoryRouter>);
    // "Broadband" appears as both the next-due stat and the card title.
    await screen.findAllByText("Broadband");
    // Section header from the category's parent, paused item separate.
    expect(screen.getByText("Household Bills")).toBeTruthy();
    expect(screen.getByText("Gym")).toBeTruthy();
    expect(screen.getByText(/Paused \(1\)/)).toBeTruthy();
    // Cadence + next-due summary text.
    expect(screen.getByText("Monthly · day 14")).toBeTruthy();
  });

  it("pauses via DELETE and resumes via activate", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Bills /></MemoryRouter>);
    await user.click(await screen.findByLabelText("Pause Broadband"));
    await waitFor(() => expect(
      calls.some((c) => c.method === "DELETE" && c.url.endsWith("/commitments/1")),
    ).toBe(true));
    await user.click(await screen.findByLabelText("Resume Gym"));
    await waitFor(() => expect(
      calls.some((c) => c.method === "POST" && c.url.endsWith("/commitments/2/activate")),
    ).toBe(true));
  });

  it("posts the next occurrence with horizon = next_date", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Bills /></MemoryRouter>);
    await user.click(await screen.findByLabelText("Post next Broadband"));
    await waitFor(() => {
      const c = calls.find((x) =>
        x.method === "POST" && x.url.includes("/commitments/1/materialise"));
      expect(c).toBeDefined();
      expect(c!.url).toContain("horizon=2026-05-14");
    });
  });

  it("creates a bill with the required fields", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Bills /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: "Add bill" }));
    await user.type(screen.getByLabelText("Name"), "Water");
    await user.selectOptions(screen.getByLabelText("Paid from"), "1");
    await user.type(screen.getByLabelText("Amount in pounds"), "45");
    await user.click(screen.getByRole("button", { name: "Add bill" }));
    await waitFor(() => {
      const c = calls.find((x) =>
        x.method === "POST" && x.url.endsWith("/api/v1/commitments"));
      expect(c).toBeDefined();
      const body = JSON.parse(c!.body);
      expect(body.name).toBe("Water");
      expect(body.account_id).toBe(1);
      expect(body.amount).toBe("-45");
      expect(body.frequency).toBe("monthly");
      expect(body.end_date).toBeNull();
      expect(body.occurrence_count).toBeNull();
    });
  });

  it("'A set number of times' maps to occurrence_count", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Bills /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: "Add bill" }));
    await user.type(screen.getByLabelText("Name"), "Loan");
    await user.selectOptions(screen.getByLabelText("Paid from"), "1");
    await user.type(screen.getByLabelText("Amount in pounds"), "150");
    await user.click(screen.getByRole("radio", { name: "A set number of times" }));
    await user.type(screen.getByLabelText("Number of payments"), "12");
    await user.click(screen.getByRole("button", { name: "Add bill" }));
    await waitFor(() => {
      const c = calls.find((x) =>
        x.method === "POST" && x.url.endsWith("/api/v1/commitments"));
      const body = JSON.parse(c!.body);
      expect(body.occurrence_count).toBe(12);
      expect(body.end_date).toBeNull();
    });
  });

  it("'Until a date' maps to end_date", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Bills /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: "Add bill" }));
    await user.type(screen.getByLabelText("Name"), "Season ticket");
    await user.selectOptions(screen.getByLabelText("Paid from"), "1");
    await user.type(screen.getByLabelText("Amount in pounds"), "80");
    await user.click(screen.getByRole("radio", { name: "Until a date" }));
    const end = await screen.findByLabelText("Stops after");
    await user.type(end, "2027-06-30");
    await user.click(screen.getByRole("button", { name: "Add bill" }));
    await waitFor(() => {
      const c = calls.find((x) =>
        x.method === "POST" && x.url.endsWith("/api/v1/commitments"));
      const body = JSON.parse(c!.body);
      expect(body.end_date).toBe("2027-06-30");
      expect(body.occurrence_count).toBeNull();
    });
  });
});
