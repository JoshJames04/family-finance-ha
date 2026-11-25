import type { components } from "../api/schema";

type Movement = components["schemas"]["ExpectedMovementOut"];

/** Project the unpaid portion with the original cash-flow direction. */
export function outstandingMovements(items: Movement[]): Movement[] {
  return items.flatMap(item => {
    if (item.dismissed_at != null) return [];
    const remaining = item.outstanding_pence ?? Math.abs(item.amount_pence);
    if (remaining === 0 && item.amount_pence !== 0) return [];
    return [{ ...item, amount_pence: Math.sign(item.amount_pence) * remaining }];
  });
}
