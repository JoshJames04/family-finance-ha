# Salary, piecework and payslips

## Payday rules

Employment payday rules are stored as compact JSON. Numeric month dates use
the previous Friday when they fall at a weekend. Named weekdays are exact
named weekdays, weekly rules run on the configured weekday, and nightly
piecework requires logged work dates.

Year preparation is available from Settings. It is confirmation-gated and
idempotent. A nightly PAYE employment is rejected until it is configured for
weekly aggregation; gross-only is an explicit cash-in-hand mode and is not a
tax exemption.

## Piecework

Piecework shifts record hours, a snapshotted hourly rate, optional tips, and
any number of labelled delivery charges. Imported historical Piecework Co
transactions retain their source transaction and exact imported total and
remain review-marked. Historical source data is never remapped automatically.

## Payslips

Payslips are private files below `FF_DATA_DIR/payslips`, limited to 10 MiB,
validated by file signature, and addressed with randomized server filenames.
PDF text extraction is local and advisory. Images or PDFs without a usable
text layer fall back to manual entry. Extracted values must be reviewed,
edited, and explicitly confirmed before actual payroll components are applied.

If local OCR tooling is unavailable, manual component entry remains supported.
OCR and PDF parsing must remain bounded and must not log payslip contents.

## Migration and rollback

The payslip and piecework changes add sequential migrations. Validate them
against a disposable synthetic database with an upgrade, one-step downgrade,
and upgrade before restarting an existing local application database. Do not
apply historical import confirmation to an existing household without an
explicit owner decision.
