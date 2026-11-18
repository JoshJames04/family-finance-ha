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

## Reviewing forecasts

Configuration changes, overtime approvals/edits and confirmed corrections mark
affected forecasts for review without changing their amounts. Use Salary → Breakdown
→ **Review forecasts**. Preview makes no persistent changes. **Apply forecast changes**
requires the same saved inputs and preserves run identity. Changed inputs require
a new preview. Actuals, correction runs, bank matches and manual overrides are
preserved and reported as exclusions. Dependent ambiguous history stops the rebuild.

Existing forecasts are marked for review on upgrade. Later formula/rule-version
changes are detected during projection repair and cumulative calculation; they
flag estimates without changing their amounts. Rebuilding is deliberate,
not a silent rewrite. Reconciled history always requires a linked correction, including the legacy
Reopen action. A piecework forecast retains its identity when work entries change;
removing its last entry leaves a zero forecast instead of deleting its identity.

## Five local manual checks

1. **Historical evidence:** open a confirmed month in Salary → Breakdown. Check
   gross, net, component signs and taxable/YTD figures against its payslip. For a
   month with an advance, confirm net entitlement and matched bank deposits remain distinct.
2. **Salary and payday:** on a test employer choose equal monthly instalments.
   Compare a February and March full-month forecast: base salary must be identical.
   Check a regular weekend-shifted payday keeps its actual cash date.
3. **Confirmation guard:** try confirming a test forecast with missing values or
   deliberately incorrect printed net. It must refuse. Enter all signed components,
   correct printed totals and taxable pay; confirm successfully and check the totals.
4. **Overtime:** approve a test entry in a cutoff window. The existing forecast must
   show a review warning without its amount silently changing. Editing an approved
   entry returns it to draft; a later salary revision must not change its saved rate.
5. **Rebuild:** preview forecast changes, cancel and check amounts are unchanged.
   Preview again and apply. Eligible forecasts update; confirmed historical actuals,
   bank matches and manual overrides stay intact. Changing inputs after preview
   must require a fresh preview.

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
