# Payroll reliability and local acceptance

Payroll forecasts and confirmed payslip facts are separate records of evidence.
An estimate is never made an actual merely because its payment date has passed.
Recorded historical payments are not recalculated on an upgrade.

## Calculation contract

- PAYE follows the numeric-code free-pay blocks, whole-pound taxable-pay rounding
  band selection and regulatory withholding limit in HMRC PAYE routines v24. The tax code already
  reflects HMRC's allowance adjustments; no second automatic allowance taper is applied.
- NI uses published weekly/monthly thresholds, not the annual threshold divided
  by the number of periods. Exact-percentage contributions round to a penny, with
  an exact half-penny rounded down.
- Cumulative BR/D codes include earlier taxable pay and PAYE; cumulative NT
  refunds prior PAYE. W1/M1 codes keep a period-only basis.
- Loans use the Class 1 NI earnings basis and whole-pound deductions. Plan 5
  collection is zero before its introduction on 6 April 2026. An ordinary
  net-pay pension reduces taxable pay but does not reduce NI or loan earnings.
- Salary calculation is explicit per employer: existing employers retain annual
  salary / 365 days until changed. Equal monthly instalments use annual / 12,
  with calendar-day proration for partial months and effective-dated salary changes.
  The overtime cutoff selects work entries, independently of the calendar month
  used for equal monthly base salary. Final approved cutoff-window overtime is
  retained after employment ends, even when the next payroll month has no base pay.
- A regular shifted cash payday retains its configured nominal PAYE date when
  the cash date exactly matches the configured shift. Irregular payments retain
  their cash date. Bank transactions always retain their real dates.
- Only monthly and weekly calculations are implemented. Unpublished historical
  years, unknown NI categories and unknown loan plans fail explicitly. Future
  estimates may explicitly use older published rules, with the rule year pinned.
  Legacy slash-labelled imports never displace published canonical rule versions.
  Ambiguous unsigned positive unpaid leave must be reviewed before rebuilding.

New rule versions append published period thresholds without changing previous
parameter rows. Calculation snapshots identify the formula method and the actual
inputs used. Older outputs remain stored as historical provenance; this does not
claim the old approximations can be rerun by the new method.

## Confirming actuals

Enter signed aggregate amounts for every component kind, including explicit zero
values, and independently enter the printed gross and net entitlement. Missing
components are not assumed to match. An aggregate is allocated once even if the
forecast contains multiple lines of the same kind. Original forecast lines remain
available for variance reporting. Positive refunds retain their sign.

PAYE employers must enter taxable pay separately. Gross-only employers may leave
unreported taxable pay blank; no value is invented. NI earnings and the paired taxable/PAYE YTD figures
are optional when unavailable; an unknown amount is not zero. A pension refund
may be reported in gross additions without being taxable. YTD uses verified facts,
never a reconstruction using the employer's current pension settings. A verified
cumulative payslip can anchor YTD even if earlier months are absent. Missing
scheduled months/weeks after that anchor block dependent forecasts. An evidenced
statutory PAYE date can be entered when correcting ambiguous early payments. Ambiguous
actual history blocks dependent cumulative forecasts until reviewed.

Net entitlement, main bank deposit and advances are different amounts. Use printed
net entitlement in confirmation. Match one or more bank receipts separately using
the existing expected-payment matching workflow. Confirmation does not assert that
a bank payment has been independently verified.

### Recovering previously verified legacy inputs

An identified payroll owner can attach missing verified payslip metadata to an
already reconciled legacy run with
`POST /employments/{emp_id}/payroll-runs/{run_id}/verify-inputs`. Supply the printed
`payslip_gross_pence`, `payslip_net_pence`, separately verified `taxable_pence`
(required for PAYE), optional `niable_pence`, optional paired `ytd_taxable_pence`
and `ytd_tax_pence`, and the private source document's 64-character `source_sha256`.
This is an explicit evidence recovery operation; it never infers missing facts.
The digest identifies the independently reviewed source and does not upload it.

Every existing component must have an actual amount, and the signed component
totals and recorded actual totals must agree with the payslip. Ambiguous positive
legacy deductions, corrected or forecast runs and conflicting verified facts are
rejected. Independently evidenced pension returns may appear in gross additions.
Legacy payslips can present a negative leave adjustment as a post-gross deduction;
recovery accepts gross before or after that adjustment only when the independent
printed gross also matches the recorded actual gross and every component sums to
printed net. It does not reclassify the line or derive taxable pay from its label.
Existing component identities, labels, rates, actuals and forecasts are preserved,
along with run amounts and bank matching evidence. Changing actuals or previously
verified facts still requires the correction workflow. Repeating the same facts
is safe and does not create a duplicate evidence audit event.

The operation serializes with payroll writers before reading the current run and
records the actor, source digest and newly attached field names in the audit log,
without source paths, document text or financial payloads. It automatically marks
later forecasts stale and attempts the normal guarded refresh. The response
contains `run` and `refresh` (including any unresolved `issues`). Evidence remains
saved if other missing history or setup prevents forecasting; those issues must
be resolved from independent evidence. Employer salary policy remains an explicit
setting and is never inferred from recovered payslip amounts. A trusted offline
operator can use `domain.payroll_evidence.verify_inputs` against a disposable
copy with the same validation and audit behavior; direct SQL repair is unnecessary.

## Reviewing forecasts

Leave entered as days retains its original day count separately from fixed cash
deductions. Rebuilds recalculate those days using the current salary and working
week, without double-counting cash adjustments. A legacy unknown basis stays
unknown: its cash deduction is preserved and the preview warns, rather than
inventing a day count.

Configuration changes, overtime approvals/edits and confirmed corrections trigger
an automatic guarded refresh. Eligible forecasts update immediately; saving the
source change does not depend on manually previewing or applying a rebuild.
Incomplete setup or ambiguous cumulative history leaves the source change saved,
keeps the affected forecast flagged and displays an actionable reason in Breakdown.
Uploaded payslips (including unconfirmed or failed extractions), actuals, correction
runs, bank matches and manual overrides are always preserved. Adding or changing
payslip review evidence invalidates an earlier rebuild preview.
A forecast is never promoted to verified actual pay by automatic calculation.

Startup, daily maintenance and opening Breakdown maintain future payroll through
the end of the following calendar year. Missing historical months are not filled
with invented actuals. Piecework still requires logged work. Changes to formula or
rule versions invalidate eligible forecasts before recalculation. Published rules
from an earlier year remain visibly identified on provisional future forecasts.
Regular cash dates follow payday settings while explicit one-off dates retain their
saved provenance. A frequency change needs review rather than guessed new periods.
Starter cutoff periods that end before employment begins are skipped without
blocking subsequent payroll; equal-monthly salary still covers calendar-month work.
The explicit preview/apply API remains available for diagnostics and still rejects
changed inputs between preview and apply.
Clearing the payday removes unmatched projected salary estimates from the old rule;
matched evidence and recorded payroll remain protected.

Breakdown has equal monthly-payroll and employer columns on wide screens, stacking
on small screens. The year selector covers all employers. Monthly totals use the
statutory payroll pay month, not the beginning of a cutoff earning window. Add run
is no longer a routine action; optional payslip checking remains available when
independent evidence needs recording or correction.
Overtime detail combines recorded hours at the same hourly rate for all payroll
years, while different rates and partial actuals stay separate. It sums the stored
line amounts without recalculating pay or changing original payslip components.

## Five local manual checks

1. **Automatic overtime:** select the test employer's payroll year. Approve a draft
   overtime entry and return to Breakdown. Gross/net update without Review or Apply.
   Editing that entry returns it to draft and removes its pay until approved again.
2. **Automatic salary:** record a dated salary revision on the test employer. Check
   affected forecasts update and earlier confirmed history retains its actuals.
3. **Payday:** change the test employer's payday. Its regular forecast and expected
   income move together, with one income movement for that period. Weekend shifts
   retain the correct nominal payroll month.
4. **Compact view:** select two payroll years. Monthly totals combine employers by
   pay month, and employer details follow the year selection. A March–April earning
   window paid in April appears in April.
5. **Evidence boundary:** reopen a known confirmed month and check gross, net and
   bank matches remain unchanged. A forecast remains labelled forecast; optional
   payslip confirmation still refuses missing components or incorrect printed net.

Automated tests use synthetic examples only. Private source replay is supplementary
evidence, not a public fixture or a claim of HMRC certification. Salary, overtime,
pension policies and missing bank history cannot be inferred from a tax formula.
## Sources

- [HMRC PAYE tax table routines v24](https://www.gov.uk/government/publications/payroll-technical-specifications-income-tax), sections 4.3–4.5 and 5.4.
- [HMRC employer rates 2026/27](https://www.gov.uk/guidance/rates-and-thresholds-for-employers-2026-to-2027).
- [CWG2 employer guide 2026/27](https://www.gov.uk/government/publications/cwg2-further-guide-to-paye-and-national-insurance-contributions/2026-to-2027-employer-further-guide-to-paye-and-national-insurance-contributions), rounding and regular-payday treatment.
- [HMRC SL3 2026/27](https://www.gov.uk/government/publications/sl3-student-loan-deduction-tables/2026-to-2027-student-and-postgraduate-loan-deduction-tables).

The backend session fixture migrates an empty database once per test worker and
copies it for each test. No test data is shared; explicit migration roundtrips
remain independent tests.

The API contract fuzzer rejects synthetic bank credentials locally. It does not
send generated credentials to a real banking provider; successful provider and
outage behaviour are covered by the dedicated banking tests.

Employer rows show pay date, net and status. Expand a row to see its earning period,
gross, component details, forecast variance and optional amount-checking action.

Automatic setup scans the preceding nominal period when a forward payday shift
lands in today. Final cutoff windows are tested against the earning window, so
leavers retain payable final salary/overtime without an invented empty month.

Automatic refresh starts at the earliest affected forecast and recalculates later dependent runs in pay order. The background scheduler checks forecasts hourly. Automatic rebuilds preserve an incomplete forecast if its source inputs cannot reproduce its stored gross after accounting for approved overtime changes, and leave it flagged for review. Bonus edits can recover a missing derived base salary line when the guarded rebuild accounts for the original gross. Protected history requires review.
