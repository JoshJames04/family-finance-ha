# Changelog — Family Finance add-on

## 1.1.2

- 2026-27 UK payroll rule sets — current-tax-year payroll uses exact
  published parameters.
- Ingress identity hardening: no `X-Profile-Id` or localStorage
  identity trusted under ingress.
- Observability: operations runbook (`docs/OPERATIONS.md`), release
  gate + perf tooling, ingress trust audit matrix.

## 1.1.1

- Add-on card keeps the name **Family Finance**; the HACS integration
  is now **Family Finance (Entities)** so the two update cards are
  distinguishable.
- This changelog file ships with the add-on — update cards now show
  release notes instead of "No changelog found".

## 1.1.0

- Add-on icon and logo.
- Backup list: newest first, Latest badge, readable labels, and the
  snapshot button locks while a backup runs.
- Admin action to link a Home Assistant identity to an existing
  profile.
- Bank-ledger integrity fixes; performance budgets; blocking lint and
  type checks; migration parity tooling and workbook-retirement
  sign-off.

## 1.0.5

- Ingress `basename` derived at startup — fixes the blank white panel.
- Healthcheck `start-period=120s` — fixes the first-boot restart loop.

## 1.0.3

- Empty Supervisor options (`""`) no longer crash second boot onward.
- Empty `X-Admin-Token` no longer satisfies the comparison.
- CRLF hardening for the entrypoint on Windows checkouts.

## 1.0.2

- Ingress auth + fresh-install bootstrap fixes.

## 1.0.1

- Add-on options `admin_token`/`backup_dir` default to empty strings
  the Supervisor accepts.
- Entrypoint chowns `/data` before dropping privileges — fixes the
  root-owned `options.json` start crash.
