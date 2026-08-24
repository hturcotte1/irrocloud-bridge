# IrroCloud discovery report

Run at 2026-08-24T20:38:28.142207+00:00 against https://www.irrocloud.com.
This file is committed; screenshots/HTML/network logs stay local (gitignored) because they can contain account details.

## Devices seen


## Best-guess device → field mapping (CONFIRM BY HAND)

- `cunningham-6` → best guess: **(no close match)**
- `rv80` → best guess: **(no close match)**
- `bennett-1-n` → best guess: **(no close match)**
- `whitted` → best guess: **(no close match)**

## Export

- No export was captured — see problems below.

## Timezone evidence

- our_clock_utc: 2026-08-24T20:38:43.394467+00:00
- our_clock_boise: 2026-08-24T14:38:43.394467-06:00
- on_page_time_strings: []
- export_newest_timestamp: None
- how_to_read: If the on-page/export times match the Boise clock, exports are local time (the bridge's default assumption). If they match UTC, set TIMEZONE=UTC in .env.

## Account/settings findings (API, tokens, integrations)

- Nothing mentioning API/token/integration was seen.

## Navigation links seen


## Problems

- The login did not reach a logged-in page — wrong email or password, or the site changed. Screenshot: discovery/02-login-failed.png

## What the morning session does with this

1. Fill `irrocloud_device_name` for each field in fields.json from the mapping above (exact names).
2. Adjust SELECTORS in bridge/fetchers/irrocloud_browser.py if any step failed.
3. If network.jsonl shows a clean data request (JSON/CSV), consider STRATEGY='request' with DATA_REQUEST_TEMPLATE.
4. Confirm the export timezone and set TIMEZONE in .env if it is not Boise local.
