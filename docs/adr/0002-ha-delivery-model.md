# ADR-0002: Home Assistant Delivery Model

Status: Proposed
Date: 2026-11-06

## Context

App must appear in the HA sidebar via ingress (Grocy-style). HAOS users run
apps/add-ons; HA Container users have no add-on store.

## Options considered

- **HA app (add-on) + ingress (chosen):** native UX, sidebar, supervisor
  lifecycle, HA backups include the data volume.
- HA integration only (UI inside HA frontend): too constraining for a rich
  finance UI; couples release cadence to HA.
- Standalone container only: loses ingress/sidebar/backup integration.

## Decision

Primary: HAOS app served exclusively over ingress. Secondary: the same image
standalone (mapped port) for HA Container users. Custom integration
communicates with the app API over local HTTP in both modes.

## Consequences

- Auth must work in ingress mode (HA session) and standalone mode
  (app-managed) — see ADR-0005.
- Packaging matrix (amd64/aarch64) and ingress path handling are Epic 1
  spike outputs.

## Validation / revisit triggers

Revisit if ingress cannot carry the SPA without path/cookie issues that the
spike cannot resolve cleanly.
