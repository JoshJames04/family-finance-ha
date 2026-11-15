# Occasion dates and reminders

Gifts supports an optional **Event date** when creating an occasion and a
**Save event date** control for existing occasions. Choose the actual date within
the event's year, or clear it when unknown. A failed save keeps the entered date
visible and offers the same Save action for retry. Editing a date changes no
gift notes, planned/actual amounts or payment status.

Existing occasions migrate with no date. Christmas dates and birthdays are never
inferred from names/types. Yearly rollover deliberately leaves the new date unset
until explicitly chosen, including leap-day occasions.

`seasonal.approaching` joins the Gifts event stream once per event and configured
date within the three-day default lead window, including the date itself. The
first available scheduler tick inside that window catches up; past dates do not
replay old reminders. Setting a different date defines a different occurrence.
Clearing a date stops future reminders, but cannot retract a delivered event.

For a different lead time, opt into **Days to occasion** in the integration's
Configure screen, selecting All or one or several named occasions. Its native
numeric-state sensor uses the configured date and the app calendar date; unknown
dates create no sensor. Below `N + 1` and Above `-1` express within N days without
YAML: for seven days, enter Below `8` and Above `-1`. This separate metric defaults
to None and shares the 100-entity global cap.
Stable `seasonal:<id>:days` identities survive occasion renaming.

The default payload contains source/status, observation date, expected date and
lead days. The occasion name requires existing explicit detailed-data opt-in;
gift recipients and notes are never included. Private events retain their
original owner scope even if later made shared, and internal ownership markers
are not published. Shared/private feed restrictions remain in force: detailed
mode adds payload detail, not permission to private member records.

Actual Home Assistant visual-editor acceptance remains #441. Use a synthetic
local occasion to check create/edit/clear, one reminder inside the window,
refresh/reload without duplicates, and Safe mode before enabling details. Do not
guess dates for the existing household or change live configuration for testing.
