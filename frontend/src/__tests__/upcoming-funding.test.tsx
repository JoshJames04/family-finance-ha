import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { Upcoming } from "../pages/Upcoming";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("previews a personal funding transfer and saves one dated rule", async () => {
  const writes: { path: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = new URL(String(input), "http://localhost").pathname;
    let body: unknown = { items: [], total: 0 };
    if (init?.method === "POST") {
      writes.push({ path, body: JSON.parse(String(init.body)) });
      body = { id: 5 };
    } else if (path.endsWith("/households")) body = { items: [{ id: 1, name: "Home" }], total: 1 };
    else if (path.endsWith("/accounts")) body = { items: [
      { id: 1, name: "Main", owner_profile_id: 3, archived: false },
      { id: 2, name: "Bills space", owner_profile_id: 3, archived: false },
      { id: 3, name: "Closed", owner_profile_id: 3, archived: true },
      { id: 4, name: "Other member", owner_profile_id: 4, archived: false },
    ], total: 4 };
    else if (path.endsWith("/expected-movements")) body = [];
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  });
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={["/upcoming?tab=transfers"]}><Upcoming /></MemoryRouter>);
  await user.click(screen.getByRole("button", { name: "Schedule transfer" }));
  const dialog = within(await screen.findByRole("dialog"));
  await user.type(dialog.getByLabelText("Name"), "Bills funding");
  await user.type(dialog.getByLabelText(/Amount/), "123.45");
  fireEvent.change(dialog.getByLabelText("First transfer"), { target: { value: "2090-01-31" } });
  const to = dialog.getByLabelText("Transfer to");
  expect(within(to).queryByRole("option", { name: "Main" })).toBeNull();
  expect(within(to).queryByRole("option", { name: "Closed" })).toBeNull();
  expect(within(to).queryByRole("option", { name: "Other member" })).toBeNull();
  await user.selectOptions(to, "2");
  expect(dialog.getByText(/123.45 from Main to Bills space/)).toBeTruthy();
  expect(writes).toHaveLength(0);
  await user.click(dialog.getByRole("button", { name: "Schedule transfer" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(writes).toEqual([{ path: "/api/v1/transfer-rules", body: {
    name: "Bills funding", source_account_id: 1, dest_account_id: 2,
    amount_spec: { kind: "fixed", pence: 12345 }, frequency: "monthly",
    day_of_month: 31, start_date: "2090-01-31",
  } }]);
});
