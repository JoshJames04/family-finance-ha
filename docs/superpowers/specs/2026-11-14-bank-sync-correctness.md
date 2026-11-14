# Bank sync correctness (#490)

The same SQLite-backed bank connection and account/source must have one active sync owner. Native nonblocking advisory locks release on normal exit, exception or process termination; a persisted running cursor alone must not permanently block restart recovery. Locks cover HTTP without holding a SQLite writer. Concurrent manual requests queue intent; scheduled contention preserves it. Unrelated connections remain eligible.

Persist provider balance snapshots independently of ledger repair. Existing pre-opening history repair runs only after a complete successful feed, against a newly fetched balance (including uncached Spaces), never against discovery caches or a failed feed. Preserve other historical transactions, payees and amounts. Finite transient DNS/5xx retry deadlines are per-feed, while existing provider429 cooldown remains connection-wide.

Expose scheduler enabled state, hide automatic next-run promises when disabled, and keep manual refresh available. Synthetic tests establish code behavior only; #416 unattended provider acceptance and #475 private live policy remain open.
