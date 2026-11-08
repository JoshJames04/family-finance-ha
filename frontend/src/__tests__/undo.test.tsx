/** #290: undo affordances — destructive-but-reversible actions surface
 * an Undo button on the success toast that runs the revert call. Covers
 * the category archive toggle (the reported misclick) and the
 * categorisation-rule recreate path. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Toaster, toast } from "sonner";
import { Settings } from "../pages/Settings";
import { Rules } from "../pages/Rules";

// jsdom lacks pointer capture; sonner's swipe-to-dismiss calls it.
Object.assign(Element.prototype, {
  setPointerCapture: () => {},
  releasePointerCapture: () => {},
  hasPointerCapture: () => false,
});

const CATEGORY = {
  id: 7, parent_id: null, name: "Groceries",
  is_system: false, archived: false,
};
const RULE = {
  id: 3, household_id: 1, pattern: "tesco",
  match_type: "contains", category_id: 7, priority: 50,
  auto_apply: true, times_confirmed: 4, enabled: true,
  source: "manual",
};

function stubFetch(calls: { url: string; method: string; body: string }[]) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: String(init?.body ?? "") });
      const body = method !== "GET"
        ? { ok: true }
        : url.includes("/api/v1/households")
          ? { items: [{ id: 1, name: "Home" }], total: 1 }
          : url.includes("/api/v1/categories")
            ? { items: [CATEGORY], total: 1 }
            : url.includes("/categorisation/rules")
              ? [RULE]
              : url.includes("/api/v1/profiles")
                ? { items: [{ id: 1, display_name: "Josh", role: "admin",
                    is_active: true, color: null }], total: 1 }
                  : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

describe("Undo affordances", () => {
  afterEach(() => { toast.dismiss(); cleanup(); });

  it("archiving a category offers Undo that restores it", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Toaster /><Settings /></MemoryRouter>);
    await user.click(
      await screen.findByLabelText(/Archive Groceries/i));
    await waitFor(() => expect(
      calls.some((c) => c.method === "PATCH"
        && c.url.includes("/categories/7")
        && c.body.includes('"archived":true')),
    ).toBe(true));
    await user.click(await screen.findByRole("button", { name: "Undo" }));
    await waitFor(() => expect(
      calls.some((c) => c.method === "PATCH"
        && c.url.includes("/categories/7")
        && c.body.includes('"archived":false')),
    ).toBe(true));
  });

  it("deleting a rule offers Undo that recreates it", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Toaster /><Rules /></MemoryRouter>);
    await user.click(
      await screen.findByLabelText(/Delete rule .tesco./i));
    await waitFor(() => expect(
      calls.some((c) => c.method === "DELETE"
        && c.url.includes("/categorisation/rules/3")),
    ).toBe(true));
    await user.click(await screen.findByRole("button", { name: "Undo" }));
    await waitFor(() => expect(
      calls.some((c) => c.method === "POST"
        && c.url.includes("/categorisation/rules")
        && c.body.includes('"pattern":"tesco"')),
    ).toBe(true));
  });
});
