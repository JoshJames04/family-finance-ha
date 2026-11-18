# Employer bonuses and shareholdings

In Salary, each employer has a **Share payment method** and dated salary/share
history. A new salary revision defaults to the holding effective on its selected
date. Changing that date updates the default until a holding is explicitly edited.
Check the date and holding when recording a historical or future change.
Existing rows start with zero shares; no holding is guessed from transactions.

Use **Add bonus** next to **New salary** in employer configuration. Choose the
payment month and a bonus type; only its value field is shown. Each new entry adds one of:

- A percentage of annual salary effective on the adjusted payment date.
- Shares held on that date multiplied by the entered value per share.
- A manually entered gross amount.

For example, 250 shares at £15 add £3,750 gross. Entries in the same month add
together in one payroll run. Existing combined entries retain all their parts when
edited; the form states what will be kept. Switching the type of a new or single-part
entry clears hidden values, so an invisible amount cannot change its gross pay. The configured monthly payday, cutoff and employment
dates apply, including early bank-day adjustments. Weekly/nightly pay needs a
dated payroll run instead of this monthly action. Unknown future bonuses are not
invented or repeated into later years: add their values when known.
Final pay can fall after the employment end date when the earning period overlaps
employment. A clipped cutoff period retains its original payroll month, so its
bonus is included once and a previous month's bonus is not repeated.

Each entry saves its salary, holding, share method, payment date and calculated
parts. Changing salary history or employer settings does not silently rewrite that
snapshot; explicitly edit an open bonus to recalculate it. Changing the payment
month requires deleting/recreating the open entry. Reconciled payroll, actual
components, pending corrections, bank matches, incomplete imported earnings and
manual overrides are protected. The planner changes eligible forecasts; use
explicit payslip correction for confirmed amounts. Later reconciled pay also prevents
rewriting an earlier cumulative tax basis.
Linked corrections retain the original payroll-month label, including final cutoff
periods ending in the preceding month. Protection is checked before any bonus edit.

The month uses the existing PAYE/NI, pension and student-loan configuration. Later
open forecasts recalculate cumulative PAYE after an earlier bonus changes. Net
expected income replaces the same payroll occurrence; no second salary is added.
Future unpublished tax years use the newest available rule set and the run shows
the rule year used. These are forecasts until reconciled against an actual payslip.

Share methods are employer-specific:

- **Cash paid and taxed through payroll:** the share value adds to taxable gross
  and cash pay, just like the cash percentage/manual bonus.
- **Non-cash award taxed through payroll:** the value is taxable but deducted
  from cash net as a separate non-cash component. It does not become spendable
  income. Normal configured pension treatment still applies; this option is for
  awards treated as taxable pay on the payslip, not every tax-advantaged scheme.
- **Handled outside payroll:** share values cannot be added to payroll here.
  Record sale proceeds separately; payroll must not guess Capital Gains Tax,
  acquisition costs or share-scheme exemptions. Cash bonuses remain available.
  If an existing entry contains payroll shares, changing the employer to this method
  blocks saving that entry rather than silently removing its saved share value.

See [HMRC cash bonus guidance](https://www.gov.uk/expenses-and-benefits-bonuses/what-to-report-and-pay)
and [employee share schemes](https://www.gov.uk/tax-employee-share-schemes).

Only the payroll owner can mutate bonus entries. Home Assistant read grants do
not grant write access. Tests use synthetic employers/amounts, never live payroll.

## Upgrade and review order

Review and merge payroll hardening PR #543 before bonus PR #540. Bonus migration
`0074_employer_bonuses` follows `0073_payroll_verified_inputs`; unrelated occasion-date
work is not a prerequisite. Both PRs target develop; the bonus branch includes the
engine prerequisite so it can be tested independently before review.
Back up before upgrade. A schema downgrade refuses to drop recorded bonus history;
restore a verified pre-upgrade backup for rollback instead of deleting history.
