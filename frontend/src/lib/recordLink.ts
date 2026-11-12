/** Exact source identity carried from a projected movement into its page. */
export function movementLink(m: { id: number; source_type: string; source_id: number | null }): string {
  const id = m.source_id;
  switch (m.source_type) {
    case "commitment": return `/bills#commitment-${id}`;
    case "employment": return `/salary?employment_id=${id}#employment-${id}`;
    case "payroll_run": return `/salary?run_id=${id}#payroll_run-${id}`;
    case "savings_goal": return `/goals#savings_goal-${id}`;
    case "subscription": return `/subscriptions#subscription-${id}`;
    case "transfer_rule": return `/upcoming?tab=transfers#transfer_rule-${id}`;
    default: return `/upcoming#movement-${m.id}`;
  }
}
