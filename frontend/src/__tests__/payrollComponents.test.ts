import { describe, expect, it } from "vitest";
import { groupOvertimeComponents } from "../lib/payrollComponents";
import type { components } from "../api/schema";

type Component = components["schemas"]["PayrollComponentOut"];
const line = (id:number, label:string, amount:number, actual:number|null = null):Component => ({
  id, label, kind:"overtime", forecast_pence:amount, actual_pence:actual,
  variance_pence:actual === null ? null : actual - amount,
});

describe("overtime display groups", () => {
  it("adds decimal hours exactly and retains line rounding and actual variance", () => {
    const source = [line(1,"0.1 hours @ £12.345",123,124),
      line(2,"0.2 hours @ £12.3450",247,249)];
    expect(groupOvertimeComponents(source)).toEqual([
      {...source[0], label:"0.3 hours @ £12.345", forecast_pence:370,
        actual_pence:373, variance_pence:3},
    ]);
    expect(source[0]!.forecast_pence).toBe(123);
    expect(source[0]!.label).toBe("0.1 hours @ £12.345");
  });

  it("preserves separate rates, partial actuals, unknown labels and other component kinds", () => {
    const source = [line(1,"1 hour @ £12.345",1235),
      line(2,"2 hours @ £12.345",2469,2469), line(3,"2 hours @ £12.346",2469),
      line(4,"Approved overtime",100), {...line(5,"1 hour @ £12.345",1235),kind:"allowance" as const}];
    expect(groupOvertimeComponents(source)).toEqual(source);
  });
});
