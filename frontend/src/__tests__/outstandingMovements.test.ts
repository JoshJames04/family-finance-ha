import { expect, it } from "vitest";
import { outstandingMovements } from "../lib/outstandingMovements";

it("uses unpaid cash flow in either direction and keeps variable estimates", () => {
  const movement = { id: 1, amount_pence: -5000, outstanding_pence: 1500 };
  const items = [movement, { ...movement, amount_pence: 5000 },
    { ...movement, outstanding_pence: 0 }, { ...movement, dismissed_at: "2026-01-01" },
    { ...movement, amount_pence: 0, outstanding_pence: 0 }];
  const result = outstandingMovements(items as Parameters<typeof outstandingMovements>[0]);
  expect(result.map(row => row.amount_pence)).toEqual([-1500, 1500, 0]);
  expect(movement.amount_pence).toBe(-5000);
});
