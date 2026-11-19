# Overview salary account (#544)

The owner selected automatic use of the signed-in member's current salary payment
account. If no usable salary account exists or current jobs use distinct accounts,
prompt for an account. Several jobs paid to the same account remain unambiguous.
The reported defect is that a signed-in member currently sees another member's
account in both Safe to spend and Balance forecast.

Use current employment ownership, active/start/end dates and existing account
visibility. Ignore future/ended jobs and archived or inaccessible accounts.
Resolve identity before selecting or requesting a projection. Loading and failed
reads must remain distinguishable from missing configuration. Under HA ingress,
the authenticated identity overrides browser-stored profile IDs.

Implementation choice: keep one account selection for the two Overview widgets.
Manual switching applies during the visit; revisiting derives the salary default
again. An ambiguous/missing default presents Choose an account and no projection.
Drop invalid manual selections safely when scope or account availability changes.

A separate main-account setting and remembering the last selection were considered;
the owner chose salary-based selection. Reuse existing controls and API reads,
without changing payroll destinations, adding dependencies or migrating data.
Other pages keep their existing projection-selection behaviour.

Use synthetic tests for member isolation, ingress identity, common/distinct salary
accounts, dates, visibility, asynchronous loading/errors and manual switching.
