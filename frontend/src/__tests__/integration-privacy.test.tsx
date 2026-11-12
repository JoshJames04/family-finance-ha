import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { IntegrationPrivacy } from "../pages/Settings";

vi.mock("../api/hooks", () => ({ useReady: () => true }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("requires explicit privacy confirmation before enabling detailed HA events", async () => {
  const writes: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
    if (init?.method === "PATCH") writes.push(String(init.body));
    return new Response(JSON.stringify({ detail: "safe" }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  const user = userEvent.setup();
  render(<IntegrationPrivacy admin />);
  const select = await screen.findByLabelText("Home Assistant event detail");
  await user.selectOptions(select, "detailed");
  expect(writes).toHaveLength(0);
  expect(screen.getByRole("dialog")).toBeDefined();
  expect(screen.getByText(/recorder, automation traces and notification recipients/)).toBeDefined();
  await user.click(screen.getByRole("button", { name: "Enable detailed events" }));
  expect(writes).toEqual([JSON.stringify({ detail: "detailed" })]);
});
