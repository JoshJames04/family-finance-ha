import type { components } from "../api/schema";

type Component = components["schemas"]["PayrollComponentOut"];
const OVERTIME = /^(\d+(?:\.\d+)?) hours? @ £(\d+(?:\.\d+)?)$/;
const normaliseDecimal = (value: string) => value.includes(".")
  ? value.replace(/0+$/, "").replace(/\.$/, "") : value;

function addHours(left: string, right: string): string {
  const [a = "0", af = ""] = left.split(".");
  const [b = "0", bf = ""] = right.split(".");
  const places = Math.max(af.length, bf.length);
  const total = BigInt(a + af.padEnd(places, "0")) + BigInt(b + bf.padEnd(places, "0"));
  const digits = total.toString().padStart(places + 1, "0");
  return places ? normaliseDecimal(`${digits.slice(0, -places)}.${digits.slice(-places)}`) : digits;
}

/** Display only: retain source lines and sum their recorded pence, never recalculate pay. */
export function groupOvertimeComponents(source: readonly Component[]): Component[] {
  const result: Component[] = [];
  const groups = new Map<string, Component>();
  for (const component of source) {
    const match = component.kind === "overtime" ? OVERTIME.exec(component.label.trim()) : null;
    if (!match) {
      result.push({ ...component });
      continue;
    }
    const rate = normaliseDecimal(match[2]!);
    // Partial actuals must not be presented as a fully verified group.
    const key = `${rate}:${component.actual_pence === null ? "forecast" : "actual"}`;
    const combined = groups.get(key);
    if (!combined) {
      const copy = { ...component };
      groups.set(key, copy);
      result.push(copy);
      continue;
    }
    const previousHours = OVERTIME.exec(combined.label.trim())![1]!;
    combined.label = `${addHours(previousHours, match[1]!)} hours @ £${rate}`;
    combined.forecast_pence += component.forecast_pence;
    if (combined.actual_pence !== null && component.actual_pence !== null)
      combined.actual_pence += component.actual_pence;
    combined.variance_pence = combined.variance_pence !== null && component.variance_pence !== null
      ? combined.variance_pence + component.variance_pence : null;
  }
  return result;
}
