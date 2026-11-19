import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { IntegrationPrivacy, IntegrationGrants } from "../pages/Settings";

vi.mock("../api/hooks", () => ({ useReady: () => true }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("requires explicit privacy confirmation before enabling detailed HA events", async () => {
  const writes: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (init?.method === "PATCH") writes.push(String(init.body));
    const body = String(input).includes("private-grants")
      ? { profile_ids: [], account_ids: [], profiles: [], accounts: [] } : { detail: "safe" };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  const user = userEvent.setup();
  render(<IntegrationPrivacy admin />);
  const select = await screen.findByLabelText("Home Assistant event detail");
  await user.selectOptions(select, "detailed");
  expect(writes).toHaveLength(0);
  expect(screen.getByRole("dialog")).toBeDefined();
  expect(within(screen.getByRole("dialog")).getByText(/recorder, automation traces and notification recipients/)).toBeDefined();
  await user.click(screen.getByRole("button", { name: "Enable detailed events" }));
  expect(writes).toEqual([JSON.stringify({ detail: "detailed" })]);
});

it("saves only named opt-in read grants after privacy confirmation and can revoke", async () => {
  const writes: unknown[] = [];
  let saved = { profile_ids: [] as number[], account_ids: [] as number[] };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
    if (init?.method === "PUT") {
      saved = JSON.parse(String(init.body)); writes.push(saved);
    }
    return new Response(JSON.stringify({ ...saved,
      profiles: [{ id: 2, name: "Owner" }], accounts: [{ id: 3, name: "Private pay" }] }),
      { status: 200, headers: { "Content-Type": "application/json" } });
  });
  const user = userEvent.setup();
  render(<IntegrationGrants />);
  const owner = await screen.findByLabelText("Payroll for Owner");
  expect((owner as HTMLInputElement).checked).toBe(false);
  await user.click(owner);
  await user.click(screen.getByLabelText("Account Private pay"));
  await user.click(screen.getByRole("button", { name: "Save read access" }));
  expect(writes).toHaveLength(0);
  expect(screen.getByRole("dialog")).toBeDefined();
  await user.click(screen.getByRole("button", { name: "Confirm read access" }));
  expect(writes).toEqual([{ profile_ids: [2], account_ids: [3] }]);
  await user.click(await screen.findByRole("button", { name: "Revoke all private access" }));
  expect(writes[1]).toEqual({ profile_ids: [], account_ids: [] });
});
