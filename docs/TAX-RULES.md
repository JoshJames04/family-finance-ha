# Tax rule sets (issue #81)

All PAYE, National Insurance, student-loan and pension calculations are
driven by **versioned rule sets** — never by constants in code. The
engine in `backend/app/domain/tax.py` is a pure function of
`(gross, tax_code, params, period, ytd, config) -> Deductions`.

## Storage model

- `tax_rule_set` table — `(tax_year, jurisdiction, version)` unique key,
  `params` JSON blob. Rows are **immutable**: a correction lands as a
  new version row; new tax years append new rows.
- `backend/app/domain/data/uk_tax_rules.json` — the embedded,
  source-of-truth parameter file. `seed_rule_sets()` inserts any sets
  missing from the table and never edits existing rows (idempotent).
- `rule_set(session, tax_year, jurisdiction)` returns the **latest
  version** for a year/jurisdiction, seeding on demand.

## Parameter format

All money is integer **pence per annum**; rates are **basis points**
(2000 = 20%); monthly thresholds derive as `annual // 12` and every
deduction floors to the penny per HMRC convention.

```json
{
  "personal_allowance": 1257000,
  "taper_start": 10000000,
  "taper_divisor": 2,
  "bands": [{"limit": 3770000, "rate": 2000}, {"limit": null, "rate": 4500}],
  "ni": {"primary_threshold": 1257000,
         "upper_earnings_limit": 5027000,
         "categories": {"A": [below_rate_bps, above_uel_bps]}},
  "student_loans": {"plan2": [threshold_pence, rate_bps]}
}
```

## Authoritative sources

Each rule set records parameters from the published HMRC/GOV.UK values
for that year:

- Income Tax rates, allowances and PA taper:
  https://www.gov.uk/income-tax-rates
- Scottish Income Tax rates and bands:
  https://www.gov.uk/scottish-income-tax
- Class 1 employee NI thresholds, UEL and category rates:
  https://www.gov.uk/guidance/rates-and-thresholds-for-employers-2025-to-2026
- Student-loan and postgraduate-loan repayment thresholds/rates:
  https://www.gov.uk/repaying-your-student-loan/what-you-pay

## Version bump workflow

1. **New tax year** — append a `{tax_year, jurisdiction, version: 1}`
   entry to `uk_tax_rules.json` from the published GOV.UK values.
   Do not touch prior-year entries.
2. **Correction to an existing year** — append a new entry with the same
   `tax_year`/`jurisdiction` and `version = previous + 1`. `rule_set()`
   picks the highest version automatically; historical runs are
   reproduced by pinning the older version.
3. **Verify** — run `pytest backend/tests/test_tax_engine.py`. Golden
   values must be recomputed from the *published* parameters and updated
   in the same commit as the data change. The meta-test
   (`test_meta_rate_change_breaks_goldens`) proves the goldens respond
   to parameter changes — a passing suite with a wrong rate means the
   goldens themselves are stale.
4. Cut-off for a running tax year is out of scope: mid-year emergency
   changes ship as a new `version`, and consumers pin by version.

## Supported scope

- Tax years 2024-25 and 2025-26; jurisdictions `england_wales` (also
  used for Northern Ireland income tax, which shares the rUK bands) and
  `scotland`.
- Codes: `NnL/T/M/N` (+ `S`/`C` prefix), `K` codes, `BR`, `D0`, `D1`,
  `0T`, `NT`, and `W1`/`M1`/`X` non-cumulative markers.
- NI categories A, B, C, H, J, M, Z; student-loan plans 1, 2, 4, 5 and
  postgraduate.
- Pension modes: none, relief at source (no payroll effect), net pay
  (reduces taxable only), salary sacrifice (reduces tax and NI gross).
- Weekly/fortnightly pay frequencies are expressed in *periods per
  year* — the engine takes `months` pro-rata so 13/26/52-period years
  only need a period-count parameter change, not new code.
