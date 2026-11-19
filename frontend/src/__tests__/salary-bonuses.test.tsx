import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { toast, Toaster } from "sonner";
import { SalaryBonuses, type SharePaymentMethod } from "../components/SalaryBonuses";

const BONUS = {
  id: 3, employment_id: 1, pay_month: "2026-12-01", pay_date: "2026-12-24",
  label: "Year-end", salary_percentage: "10", share_value_pence: 1500,
  manual_pence: 10000, salary_basis_pence: 4500000, shares_owned: "250",
  salary_bonus_pence: 450000, share_bonus_pence: 375000, gross_pence: 835000,
};
const calls: { method: string; url: string; body: string }[] = [];
function setup(failSave = false, sharePaymentMethod: SharePaymentMethod = "payroll_cash") {
  calls.length = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const method = init?.method ?? "GET";
    calls.push({ method, url: String(input), body: String(init?.body ?? "") });
    const failed = failSave && (method === "POST" || method === "DELETE");
    return Promise.resolve(new Response(JSON.stringify(failed
      ? { title: "Conflict", detail: "Reconciled payroll cannot be changed. Correct the run first." }
      : method === "GET" ? [{ ...BONUS, noncash_pence: sharePaymentMethod === "payroll_noncash" ? 375000 : 0, share_payment_method: sharePaymentMethod }] : BONUS), {
      status: failed ? 409 : 200, headers: { "Content-Type": "application/json" },
    }));
  });
  const onChanged = vi.fn();
  render(<SalaryBonuses employmentId={1} employer="Acme" sharePaymentMethod={sharePaymentMethod} rev={0} onChanged={onChanged} />);
  return onChanged;
}
describe("employer bonuses", () => {
  afterEach(() => { cleanup(); toast.dismiss(); vi.restoreAllMocks(); });
  it("shows the dated salary and share basis alongside gross before deductions", async () => {
    setup();
    expect(await screen.findByText("Year-end")).toBeTruthy();
    expect(screen.getByText(/250 shares/)).toBeTruthy();
    expect(screen.getByText(/£45,000.00/)).toBeTruthy();
    expect(screen.getByText(/£8,350.00/)).toBeTruthy();
  });
  it("shows one bonus value and clears hidden values when the type changes", async () => {
    const changed = setup();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", {name:"Add bonus"}));
    await user.type(screen.getByLabelText("Pay month"), "2026-12");
    await user.type(screen.getByLabelText("Salary percentage (%)"), "10");
    expect(screen.queryByLabelText("Value per share (£)")).toBeNull();
    expect(screen.queryByLabelText("Manual gross amount (£)")).toBeNull();
    await user.selectOptions(screen.getByLabelText("Bonus type"), "shares");
    await user.type(screen.getByLabelText("Value per share (£)"), "15");
    await user.selectOptions(screen.getByLabelText("Bonus type"), "manual");
    await user.type(screen.getByLabelText("Manual gross amount (£)"), "100");
    await user.click(screen.getByRole("button", {name:"Save bonus"}));
    await waitFor(() => expect(changed).toHaveBeenCalled());
    const post = calls.find(c => c.method === "POST")!;
    expect(JSON.parse(post.body)).toMatchObject({pay_month:"2026-12-01", salary_percentage:null, share_value:null, manual_amount:"100"});
  });  it("prefills and patches an individual entry then deletes only that entry", async () => {
    setup();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Edit Year-end" }));
    await user.selectOptions(screen.getByLabelText("Bonus type"), "shares");
    expect((screen.getByLabelText("Value per share (£)") as HTMLInputElement).value).toBe("15");
    await user.clear(screen.getByLabelText("Bonus label"));
    await user.type(screen.getByLabelText("Bonus label"), "Updated");
    await user.click(screen.getByRole("button", { name: "Save bonus" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.url.endsWith("/bonuses/3"))).toBe(true));
    expect(JSON.parse(calls.find(c => c.method === "PATCH")!.body)).toMatchObject({salary_percentage:"10", share_value:"15", manual_amount:"100"});
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(screen.getByRole("button", { name: "Delete Year-end" }));
    await user.click(await screen.findByRole("button", {name:"Confirm removal"}));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/bonuses/3"))).toBe(true));
  });
  it.each(["payroll_cash", "outside_payroll"] as const)("confirms deletion without offering an unsafe Undo for %s", async method => {
    const changed = setup(false, method);
    render(<Toaster />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", {name:"Delete Year-end"}));
    expect(screen.getByRole("dialog").textContent).toContain("cannot be undone automatically");
    expect(calls.some(c => c.method === "DELETE")).toBe(false);
    await user.click(screen.getByRole("button", {name:"Cancel"}));
    expect(calls.some(c => c.method === "DELETE")).toBe(false);
    await user.click(screen.getByRole("button", {name:"Delete Year-end"}));
    await user.click(screen.getByRole("button", {name:"Confirm removal"}));
    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(await screen.findByText("Bonus removed")).toBeTruthy();
    expect(screen.queryByRole("button", {name:"Undo"})).toBeNull();
    expect(calls.some(c => c.method === "POST")).toBe(false);
  });
  it("keeps the confirmation open and the original entry intact when removal is rejected", async () => {
    const changed = setup(true);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", {name:"Delete Year-end"}));
    await user.click(screen.getByRole("button", {name:"Confirm removal"}));
    expect((await screen.findByRole("alert")).textContent).toContain("Correct the run first");
    expect(changed).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", {name:"Cancel"}));
    expect(screen.getByText("Year-end")).toBeTruthy();
  });
  it("retains the entry and displays an actionable payroll conflict", async () => {
    setup(true);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add bonus" }));
    await user.type(screen.getByLabelText("Pay month"), "2026-12");
    await user.selectOptions(screen.getByLabelText("Bonus type"), "manual");
    await user.clear(screen.getByLabelText("Manual gross amount (£)"));
    await user.type(screen.getByLabelText("Manual gross amount (£)"), "100");
    await user.click(screen.getByRole("button", { name: "Save bonus" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("Correct the run first"));
    expect((screen.getByLabelText("Manual gross amount (£)") as HTMLInputElement).value).toBe("100");
  });
  it("excludes outside-payroll shares from the payroll request", async () => {
    setup(false, "outside_payroll");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add bonus" }));
    await user.selectOptions(screen.getByLabelText("Bonus type"), "shares");
    expect((screen.getByLabelText("Value per share (£)") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(/Record separate share sales outside payroll/)).toBeTruthy();
    await user.type(screen.getByLabelText("Pay month"), "2026-12");
    await user.selectOptions(screen.getByLabelText("Bonus type"), "manual");
    await user.clear(screen.getByLabelText("Manual gross amount (£)"));
    await user.type(screen.getByLabelText("Manual gross amount (£)"), "100");
    await user.click(screen.getByRole("button", { name: "Save bonus" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.method === "POST")!.body).share_value).toBeNull();
  });
  it("identifies taxable non-cash awards separately from cash pay", async () => {
    setup(false, "payroll_noncash");
    expect(await screen.findByText(/Includes £3,750.00 non-cash shares/)).toBeTruthy();
    await userEvent.setup().click(screen.getByRole("button", { name: "Add bonus" }));
    await userEvent.setup().selectOptions(screen.getByLabelText("Bonus type"), "shares");
    expect(screen.getByText(/Non-cash award value per effective share/)).toBeTruthy();
  });
  it("does not silently drop an existing share part when the employer moves outside payroll", async () => {
    setup(false, "outside_payroll");
    await userEvent.setup().click(await screen.findByRole("button", {name:"Edit Year-end"}));
    expect(screen.getByRole("alert").textContent).toContain("outside payroll");
    expect((screen.getByRole("button", {name:"Save bonus"}) as HTMLButtonElement).disabled).toBe(true);
    expect(calls.some(call => call.method === "PATCH")).toBe(false);
  });
});
