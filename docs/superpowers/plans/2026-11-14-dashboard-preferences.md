# Dashboard preference recovery (#492)

## Agreed design

Validate browser and API layouts before normalization, retaining recognized string
IDs, order and explicit hidden choices. Malformed fields fall back without writing
defaults to the API. Keep the legacy anonymous browser layout readable.

Keep drafts in session storage scoped to household and member; under ingress use
the trusted `/auth/me` identity, with the mount as a startup boundary. A rejected
save remains visibly unsaved across navigation/reload, with Retry and Revert.
Do not enable remote writes before identity/layout resolution. Disable edits while
a save is pending. A bounded per-resource in-flight map extends that lock over
route remounts; completion may update only its originating scope. There is no
backend migration, dependency, or cross-tab concurrency protocol in this change.

## Tasks and checks

1. Add component regressions in `dashboard-preferences.test.tsx`: malformed local
   and remote shapes, valid hidden intent, failed PUT/remount/retry/revert,
   pending rapid-click/remount lock, resource switch and late replies. Run RED.
2. Add a focused preference hook and wire Dashboard's existing controls/status.
   Run focused tests GREEN; retain earlier hydration/layout regressions.
3. Add a synthetic browser test for rejected save, navigation, retry and reload;
   serve a disposable static bundle/API fixture, with no real backend access.
4. Update README and regression documentation. Run bounded full frontend tests,
   typecheck/build, and inspect the diff. Record evidence for parent review.
