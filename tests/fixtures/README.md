# Test fixtures policy

**All fixtures in this repository are synthetic.** No real account numbers,
balances, salaries, payees, dates-of-birth or transaction histories may ever
appear here — including in "anonymised" form derived from real data.

Guidelines:

- Use fictional people, employers, payees and institutions (e.g.
  "ACME Manufacturing Ltd", "Contoso Energy").
- Generate workbook fixtures programmatically where possible
  (`synthetic_workbook.py` — Epic 4) so structures can evolve without
  hand-editing binary files.
- Fixtures that model the real workbook's *structure* (sheet names,
  defect patterns A–F) are allowed and encouraged — structure is not
  sensitive; values are.
- If a fixture could plausibly contain a real person's data, it is wrong.
  Regenerate it.
