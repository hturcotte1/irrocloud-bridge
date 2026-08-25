# IrroCloud discovery report

Run at 2026-08-24T23:55:00.681790+00:00 against https://www.irrocloud.com.
This file is committed; screenshots/HTML/network logs stay local (gitignored) because they can contain account details.

## Devices seen

- **SB BC East** (href `csv?&id=2114`, device id **2114**, title ''); sensor labels: none seen
- **SB BC West** (href `csv?&id=2125`, device id **2125**, title ''); sensor labels: none seen
- **SB Bennett 1 N** (href `csv?&id=2424`, device id **2424**, title ''); sensor labels: none seen
- **SB Bennett 1 N HLS** (href `csv?&id=1355`, device id **1355**, title ''); sensor labels: none seen
- **SB Bennett 3** (href `csv?&id=2138`, device id **2138**, title ''); sensor labels: none seen
- **SB Bennett 3 HLS** (href `csv?&id=2071`, device id **2071**, title ''); sensor labels: none seen
- **SB Collett 2** (href `csv?&id=326`, device id **326**, title ''); sensor labels: none seen
- **SB Collette 1 W.** (href `csv?&id=1318`, device id **1318**, title ''); sensor labels: none seen
- **SB Collette 4 N** (href `csv?&id=2377`, device id **2377**, title ''); sensor labels: none seen
- **SB Cunningham 2** (href `csv?&id=2113`, device id **2113**, title ''); sensor labels: none seen
- **SB Cunningham 4** (href `csv?&id=521`, device id **521**, title ''); sensor labels: none seen
- **SB Cunningham 6** (href `csv?&id=1332`, device id **1332**, title ''); sensor labels: none seen
- **SB Cunningham 7&8** (href `csv?&id=1381`, device id **1381**, title ''); sensor labels: none seen
- **SB Lockwood PVT** (href `csv?&id=2372`, device id **2372**, title ''); sensor labels: none seen
- **SB RV 80** (href `csv?&id=2083`, device id **2083**, title ''); sensor labels: none seen
- **SB RV East E** (href `csv?&id=2118`, device id **2118**, title ''); sensor labels: none seen
- **SB RV East W** (href `csv?&id=318`, device id **318**, title ''); sensor labels: none seen
- **SB RV East WL** (href `csv?&id=1391`, device id **1391**, title ''); sensor labels: none seen
- **SB RV W.** (href `csv?&id=1379`, device id **1379**, title ''); sensor labels: none seen
- **SB Weatherby** (href `csv?&id=1224`, device id **1224**, title ''); sensor labels: none seen
- **SB Whitted** (href `csv?&id=2365`, device id **2365**, title ''); sensor labels: none seen
- **SB Whitted SS** (href `csv?&id=2254`, device id **2254**, title ''); sensor labels: none seen

## Best-guess device → field mapping (CONFIRM BY HAND)

- `cunningham-6` → best guess: **SB Cunningham 6**
- `rv80` → best guess: **SB RV 80**
- `bennett-1-n` → best guess: **SB Bennett 1 N**
- `whitted` → best guess: **SB Whitted**

## Export

- Worked on device **SB BC East**, range (full history — csv endpoint takes no dates).
- Saved as `discovery/sample-export.csv` (gitignored).
- First lines:
  ```
  Timestamp,Soil Temp,Air Temp,SM1,SM2,SM3,SM4,SM5,SM6,Switch,Rain,Batt
  2021-01-14 09:22:00+00:00,254,254,254,254,254,254,254,254,254,0,62
  2024-07-16 16:47:00+00:00,254,254,254,254,254,254,254,254,254,0,63
  2024-07-16 17:33:00+00:00,254,254,0,0,0,0,0,0,254,0,62
  2024-07-16 18:33:00+00:00,254,254,5,0,7,9,0,11,254,5,62
  2024-07-16 19:33:00+00:00,254,254,14,11,14,18,13,23,254,0,62
  2024-07-16 20:33:00+00:00,254,254,19,15,18,22,16,26,254,0,62
  2024-07-16 21:33:00+00:00,254,254,22,18,21,24,18,27,254,0,62
  2024-07-16 22:33:00+00:00,254,254,23,19,22,25,19,27,254,0,62
  2024-07-16 23:33:00+00:00,254,254,24,20,23,25,19,27,254,0,62
  ```

## Timezone evidence

- our_clock_utc: 2026-08-24T23:55:12.992668+00:00
- our_clock_boise: 2026-08-24T17:55:12.992668-06:00
- on_page_time_strings: []
- export_newest_timestamp: 2024-07-16 23:33:00+00:00
- how_to_read: If the on-page/export times match the Boise clock, exports are local time (the bridge's default assumption). If they match UTC, set TIMEZONE=UTC in .env.

## Account/settings findings (API, tokens, integrations)

- Nothing mentioning API/token/integration was seen.

## Navigation links seen

- [(no text)](/irrocloud/home)
- [(no text)](https://www.irrometer.com)
- [(no text)](javascript:void(0))
- [Register New Device](devreg)
- [(no text)](javascript:void(0))
- [(no text)](javascript:void(0))
- [(no text)](javascript:void(0))
- [(no text)](javascript:void(0))
- [(no text)](javascript:void(0))
- [(no text)](javascript:void(0))
- [(no text)](javascript:void(0))
- [(no text)](javascript:void(0))
- [(no text)](javascript:void(0))
- [(no text)](javascript:void(0))
- [(no text)](javascript:void(0))
- [Previous]()
- [1]()
- [2]()
- [3]()
- [Next]()

## Problems

- None.

## What the morning session does with this

1. Fill `irrocloud_device_name` for each field in fields.json from the mapping above (exact names).
2. Adjust SELECTORS in bridge/fetchers/irrocloud_browser.py if any step failed.
3. If network.jsonl shows a clean data request (JSON/CSV), consider STRATEGY='request' with DATA_REQUEST_TEMPLATE.
4. Confirm the export timezone and set TIMEZONE in .env if it is not Boise local.
