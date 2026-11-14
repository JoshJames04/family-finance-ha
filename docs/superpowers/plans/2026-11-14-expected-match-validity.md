# Expected-payment evidence validity (#494)

## Design

Use one SQL relation for effective match evidence. Resolve an original linked
transaction through financially equivalent correction chains (account, date,
amount, transfer and refund identity), without rewriting historical links. Follow
only void predecessors, deduplicate recursion and reject ambiguous live branches.
Require a live nonzero payment on the movement's household/account and direction;
salary sources exclude explicitly linked purchase refunds. Cap claims by the live
payment and allocate each payment's capacity once in stable match-ID order, even
when old links refer to both an original and its corrected replacement.

Expose batch `matched_totals(session, movement_ids)` and a single-ID wrapper.
Financial readers, attribution/classification and notification decisions use that
relation. Keep raw match references where they preserve audit rows/FKs during
schedule regeneration/deletion. Validate new manual/settlement writes before any
mutation. Automatic matching skips void rows. Current corrections retain refund
identity; old equivalent persisted chains resolve on reads, without a migration.

## Tasks

1. Add failing synthetic regressions for void/manual/API matching, invalid current
   financial fields, correction chains/alias double claims, salary refunds,
   auto-matching, totals/query counts, funding/category/notification consumers.
2. Implement the shared relation and route all financial evidence readers through
   it. Preserve raw audit retention and existing role/account visibility gates.
3. Run focused tests then full non-perf backend suite, ruff and mypy from an
   isolated `/tmp/fix494` copy with explicit FF_BACKEND_DIR/PYTHONPATH/data paths.
4. Update affected README/regression docs; commit only owned files as Orbit bot.
   Parent integrates with #491 projection batching and verifies combined forecast
   agreement before publishing the combined PR.
