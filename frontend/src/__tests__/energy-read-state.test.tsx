import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { Energy } from "../pages/Energy";

vi.mock("../api/hooks", () => ({ useReady: () => true }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it.each(["reading", "tariff"])("allows a second %s save", async kind => {
  let saves = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => {
    if (init?.method === "POST") saves++;
    const data = String(url).includes("/config") ? {} : [];
    return new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
  });
  const user = userEvent.setup();
  render(<Energy />);
  for (const value of ["100", "101"]) {
    await user.click(screen.getByRole("button", { name: "Add " + kind }));
    if (kind === "reading") await user.type(screen.getByRole("textbox", {name: /meter reading/i}), value);
    else {
      await user.type(screen.getByLabelText(/unit rate/i), value);
      await user.type(screen.getByLabelText(/standing charge/i), "0.50");
    }
    await user.click(screen.getByRole("button", { name: "Save " + kind }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  }
  expect(saves).toBe(2);
});

it("distinguishes a failed tariff read from an empty tariff history", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async url =>
    String(url).includes("/tariffs") ? new Response(null, {status: 403})
      : new Response(JSON.stringify(String(url).includes("/config") ? {} : []),
          {headers: {"Content-Type":"application/json"}}));
  render(<Energy />);
  await screen.findByText(/403/);
  expect(screen.queryByText("No tariffs")).toBeNull();
  expect(screen.queryByText(/Set up energy tracking/)).toBeNull();
});
