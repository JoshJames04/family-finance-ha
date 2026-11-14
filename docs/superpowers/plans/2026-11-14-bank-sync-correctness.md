# Implementation plan — #490

1. Add deterministic red regressions for active-run contention, post-ingest balance correctness/failed-feed safety, transient backoff and disabled status/UI.
2. Add shared native advisory ownership and queue contention; retain restart recovery without a migration or provider I/O under a SQLite writer.
3. Separate balance snapshots from opening repair and refresh/reconcile only successfully completed mappings using fresh snapshots; preserve existing protected transaction behavior.
4. Persist finite transient retry deadlines and expose actual scheduler state in Settings API/UI.
5. Run targeted banking tests, Ruff/Mypy and frontend typecheck/tests/build; update owning docs and report exact evidence/limitations for independent review.
