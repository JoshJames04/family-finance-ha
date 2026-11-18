# Employer bonuses and shareholdings

In Salary, each employer has a **Share payment method** and dated salary/share
history. A new salary revision carries the holding effective today into the form;
check its effective date and holding when recording a historical or future change.
Existing rows start with zero shares; no holding is guessed from transactions.

Use **Add bonus** on the employer to choose a payment month. Each entry can add:

- A percentage of annual salary effective on the adjusted payment date.
- Shares held on that date multiplied by the entered value per share.
- A manually entered gross amount.

For example, 250 shares at £15 add £3,750 gross. Entries in the same month add
together in one payroll run. The configured monthly payday, cutoff and employment
dates apply, including early bank-day adjustments. Weekly/nightly pay needs a
dated payroll run instead of this monthly action. Unknown future bonuses are not
invented or repeated into later years: add their values when known.

Each entry saves its salary, holding, share method, payment date and calculated
parts. Changing salary history or employer settings does not silently rewrite that
snapshot; explicitly edit an open bonus to recalculate it. Changing the payment
month requires deleting/recreating the open entry. Reconciled payroll, actual
components, incomplete imported earnings and manual overrides are protected;
follow the payroll correction workflow first. Later reconciled pay also prevents
rewriting an earlier cumulative tax basis.

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

See [HMRC cash bonus guidance](https://www.gov.uk/expenses-and-benefits-bonuses/what-to-report-and-pay)
and [employee share schemes](https://www.gov.uk/tax-employee-share-schemes).

Only the payroll owner can mutate bonus entries. Home Assistant read grants do
not grant write access. Tests use synthetic employers/amounts, never live payroll.

## Upgrade and review order

Prefer merging PR #535 before this feature. The identical nullable occasion-date
migration from that PR is included as the prerequisite to `0072_employer_bonuses`,
so both merge orders still converge on one migration chain. It does not infer dates.
Back up before upgrade. A schema downgrade refuses to drop recorded bonus history;
restore a verified pre-upgrade backup for rollback instead of deleting history.
