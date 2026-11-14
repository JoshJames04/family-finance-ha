# Forecast outstanding movements (#491)

Account and scenario forecasts must add only money still expected after explicit
matching. A receipt already in the ledger must not be added again on its planned
payday. Liabilities use their signed remaining amount; full or excess matching
leaves zero. Next payday and scenario payday anchors include only undismissed,
positive outstanding employment/payroll-run movements in the existing future
window. Scenario operations retain baseline payday semantics.

Reuse contribution matching's authoritative outstanding calculation. Preserve
stored movement/transaction amounts, permissions, period bounds, API shapes and
payroll replacement identities. No application data writes, provider calls,
migrations or broader match-validity policy changes belong to this fix. Inspect
void/refund/malformed historical match behavior and report any separate concern.

Acceptance: real regression tests for early partial/full/overmatched salary,
negative liabilities, dismissals, next-payday advancement, scenario and Budget
agreement, payroll replacement, public account-projection RBAC and GET read-only
behavior. Live/released acceptance under #461 remains separate.
