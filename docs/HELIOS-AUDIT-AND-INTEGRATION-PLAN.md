# HELIOS source audit + integration plan

*Written 2026-08-25, after read-only review of the real HELIOS repository
(fork `hturcotte1/HELIOS` of `marco-trotta1/HELIOS`, commit `c167a93`,
"Merge pull request #102", 2026-08-23). Nothing in HELIOS was modified.
This document is for Henry and Marco: part 1 says how faithful the bridge's
borrowed arithmetic really is, part 2 lists corrections the bridge should
make, part 3 is the plan for building ingestion into Helios itself so the
bridge can be deleted.*

---

## Part 1 — Audit: the bridge vs. the real source

The bridge was built from a prose extract (Appendix A) plus, later, the live
service's OpenAPI. With the source in hand, every borrowed rule was compared
line-for-line.

### Parser (`helios/scripts/parse_irrocloud_data.py` vs `bridge/parse.py`) — faithful

Confirmed identical: SM1..SM6 → sensor_a_12..c_18 positional mapping; modern
and legacy layout detection and column selection; the 254-sentinel mask,
negative mask, and clip at 240.0 (in the same order); drop-unparseable-
timestamps, drop-all-null rows, sort, dedupe-keep-last; depth = last
underscore token. Known deliberate difference (naive timestamps → Boise vs
UTC) is moot: real exports carry explicit offsets. Minor divergences, all
benign or bridge-stricter: the bridge accepts BOM'd files the real parser
would reject; the bridge uses a stable sort (tied duplicate timestamps can
keep a different row); the bridge raises alerts where HELIOS returns empty
frames (empty file, zero usable readings) — additions, not errors.

### Analysis (`helios/data/ingestion.py`, constants vs `bridge/analyze.py`) — right constants, three real divergences

All constants confirmed exactly: MAX_TENSION_CB 240.0, RESET_THRESHOLD_CB
15.0, ARTIFACT_DROP_THRESHOLD_CB 100.0, ARTIFACT_CEILING_FLOOR_CB 225.0,
PRECIPITATION_THRESHOLD_IN 0.0 (strict >), MAD multiplier 3.0,
ALLOWED_HORIZONS {24,48,72}. The MAD==0 no-filter quirk is real
(`ingestion.py:78`) and the bridge kept it correctly.

Real divergences (none breaks anything today; they mean the bridge's
headline numbers can differ from what HELIOS-internal analysis would say):

1. **Pooling scope.** HELIOS pools ALL SIX sensors (both depths) into one
   median/MAD population and one primary winner per timestamp
   (`parse_irrocloud_data.py:243-264`); the bridge runs the selection per
   depth (12" and 18" separately). The bridge's per-depth split matches how
   the emails present the field, but it is not HELIOS's rule.
2. **Tie-break direction.** On equal driest tension HELIOS picks the
   lexicographically GREATEST sensor id (`ingestion.py:87`, `max` over a
   tuple); the bridge picks the smallest.
3. **Wetting detection details.** (a) HELIOS has NO 3-hour continuity rule
   in `detect_wetting_events` — the bridge's gap check is an invention
   (arguably safer, but not a port; MAX_GAP is used elsewhere, for training
   windows). (b) "Every probe dropped at once" in HELIOS means all six
   columns, NaN-fails-false; the bridge tests same-depth probes with ≥2
   present. (c) HELIOS buckets precipitation by UTC day; the bridge by
   field-local day. (d) In HELIOS, missing weather is a hard error;
   "rain unknown" is a bridge invention. (e) HELIOS quarantines BOTH
   `artifact` and `ambiguous` events for training; the bridge counts an
   `ambiguous` reset as the last wetting when anchoring dry-down.

### The guessed constants — now answered

- **`MAX_PHYSICAL_SENSOR_COUNT` = 10**, and it is a cap on DISTINCT
  SENSORS per request, not on readings (`helios/agronomy/constants.py:17`,
  enforced in `schemas/inputs.py:40-49`). There is NO reading-count cap.
  The bridge's `tail(500)` guess was wrong on both number and dimension —
  harmless for Jacob's 6-sensor fields, but see Part 2 for the real risk.
- **A second, un-guessed rule exists: every sensor needs ≥ 3 readings**
  (`inputs.py:51-56`), which the bridge's global tail could theoretically
  starve.
- **`water_rights_schedule`: the browser NEVER sends `["unrestricted"]`.**
  It sends the operator's checkbox set from {tonight, tomorrow_morning,
  tomorrow_afternoon, tomorrow_night}, default
  `["tonight","tomorrow_morning"]`. This is not cosmetic: the LIST LENGTH
  is a model feature (`ingestion.py:243`), it caps allowed irrigation hours
  (`optimizer/irrigation_optimizer.py:79-81`: 3h per window, max 12), and
  the first entry becomes the saved run's `timingWindow` — so bridge runs
  currently save a timing window of literally "unrestricted", which the UI
  renders awkwardly ("soon"/"Unrestricted").
- **`future_irrigation`**: the browser omits the key; the bridge's `{}`
  produces the identical server-side default. Confirmed equivalent, and
  the earlier finding (JSON null → 422) is confirmed against source.
- **Horizon**: the browser asks for 72; the bridge asks for 24 (both legal;
  deliberate bridge choice since only the 24 h line is emailed — worth a
  conscious decision, since it changes what the model is asked).

### Run save (`src/api/run-builders.js::mapApiRun`) — key parity confirmed, five value nits

The bridge's 37-key run object exactly matches `mapApiRun`'s key set
(`run-builders.js:315-394`). The deterministic `bridge-<field>-<date>` id is
SAFE: the server upserts on the id (`routes.py:320-323`), nothing parses it,
and the UI sorts by timestamp, not id. Value-level findings:

1. **`saved: true` pins every bridge run into Jacob's "Saved Runs"** and
   the API can never un-pin (server ORs the flag). The browser sends
   `false` unless the operator opts in. Bridge should send `false`.
2. **`backendSnapshot.validationMode` must be a BOOLEAN** — the UI keeps
   only booleans (`state.js:97`); the bridge stores the string "enabled",
   which reads back as null (a badge that can never light up).
3. **The save gate self-defeats after the first success**: it templates
   against `history[0]`, which after one bridge save is the bridge's own
   run. It should template against the newest NON-`bridge-` entry (or pin
   the now-verified key set).
4. `estimatedEtIn`: the browser recomputes FAO-56 ET client-side from form
   weather; the bridge uses the server's `reference_et.value`. The bridge
   cannot do what the browser does (it sends no weather), and the server
   number is arguably more honest. Keep, documented.
5. `copyText` is derived-at-click by the UI and never read from storage —
   the bridge's version is dead weight kept only for key parity. Fine.

---

## Part 2 — Corrections the bridge should make (small, ready to apply)

None of these is breaking today — live runs succeed — but each tightens
faithfulness or removes a latent failure:

1. `water_rights_schedule`: `["unrestricted"]` → `["tonight",
   "tomorrow_morning"]` (browser default), fixing the model feature, the
   hours cap, and the saved `timingWindow` text. (Ask Jacob his real
   preferred windows; per-field config would mirror the app.)
2. `energy_price_window`: `[]` → `["tonight","tomorrow_night"]` (browser
   default) — same reasoning, milder effect.
3. `POST /web/runs` with `saved: false` (history, not pinned Saved Runs).
4. `backendSnapshot.validationMode`: map "enabled"/"disabled" → true/false.
5. Replace the `tail(500)` cap with the REAL constraints: ≤ 10 distinct
   sensors and ≥ 3 readings per sensor (cap per sensor, never globally).
6. Save-gate template: newest history entry whose id does not start with
   `bridge-`.

*(Also worth a line in DECISIONS.md: the per-depth primary series and the
3-hour wetting gap are now known to be bridge choices, not HELIOS ports —
kept deliberately because they fit a grower-facing email.)*

---

## Part 3 — Building ingestion into Helios (the plan for Marco)

### What exists today (from the source)

FastAPI app (`helios/api/main.py`) on Railway, single web service, Postgres
via SQLAlchemy Core + alembic (`preDeployCommand` runs `alembic upgrade
head`). Web accounts live in the `HELIOS_WEB_ACCOUNTS` env var (hashed
passwords, signed session cookie — no user table). `farmer_fields` stores
field setups. `sensor_snapshots` exists but only as a child of
`prediction_runs` (a copy of what each request contained) — there is no
standalone readings store. There is NO scheduler of any kind.

### What to build (four pieces, in dependency order)

1. **A readings store** — new table `sensor_readings`
   (district_id, account_email, field_id, sensor_id, ts timestamptz,
   value, measurement_type, unit, depth_in, source, quality_flag), unique
   on (account_email, field_id, sensor_id, ts) so re-ingesting the same
   full-history CSV is idempotent, exactly like the bridge's SQLite. One
   alembic migration.
2. **The fetcher** — `helios/services/irrocloud_fetch.py`. This is now
   EASY, and needs no browser: IrroCloud's login is a plain form POST
   (CSRF token + cookie) and `GET /csv?&id=<device id>` returns the
   device's entire history as a modern-layout CSV — which
   `helios/scripts/parse_irrocloud_data.py::load_irrocloud_csv` already
   parses, unchanged. Pure httpx, ~150 lines, with the bridge's operating
   rules: fetch each device once per run, never poll, stop-and-alert on
   2FA/captcha/layout change/zero rows. The device-id ↔ field mapping for
   Jacob is done and sitting in the bridge's `fields.json`.
3. **Credential custody** — the piece that is genuinely Marco's decision.
   Pilot-simple option: an env var alongside `HELIOS_WEB_ACCOUNTS`
   (e.g. `HELIOS_IRROCLOUD_CONNECTIONS = login_id:irro_user:irro_pass`).
   Product option: a `grower_connections` table with the password
   encrypted under a key held in env. Either way it moves Jacob's
   IrroCloud password out of GitHub Actions secrets and into Helios's own
   configuration. The open terms-of-service question (a product logging
   into Irrometer's site) is also Marco's/Irrigant's call — mooted the day
   Irrometer grants the API access already requested.
4. **Triggers** — two, both thin wrappers around the same service call:
   - **"Sync sensor data" button / auto-populate**: a session-authed
     `POST /web/api/sensor-sync` that runs the fetch for the logged-in
     account's devices and returns per-field newest readings; the form
     prefills `current/lagOne/lagTwo` from the last three hours instead of
     Jacob typing them. (This directly answers Henry's "populate when he
     clicks a button" question — it is exactly this endpoint plus one
     button.)
   - **Schedule**: Railway supports cron services; a second service in
     `railway.toml` running `python -m helios.scripts.morning_fetch` each
     morning gives the autopopulate-before-he-wakes behavior. (Or keep the
     bridge's GitHub Action calling the sync endpoint until Marco prefers
     otherwise.)

### Honest sizing

With the bridge as reference code and the parser already in-repo: the
migration + store ~half a day; the fetcher port ~a day; sync endpoint +
button + form prefill ~a day; cron service + config ~half a day; tests and
review margin the rest — **roughly a focused week**, most of it UI polish.

### What stays with the bridge, for now

The morning EMAIL (plain-English summary, dry-down arithmetic, trigger
countdown) is a bridge feature Helios doesn't have and doesn't need
immediately; the bridge keeps sending it. When ingestion lands in Helios,
the bridge's fetch stage flips off first; the email can migrate into Helios
later or retire when the app fully replaces it. The bridge was built to be
deleted in stages.

### Suggested sequence

1. Henry sends Marco this document (plus `docs/HANDOFF-FOR-MARCO.md`).
2. Marco decides: credential custody shape, ToS stance, cron vs button-only.
3. On his go-ahead: the work happens on the fork (`hturcotte1/HELIOS`),
   lands as a PR he reviews. Nothing merges without him.
4. Bridge keeps running mornings throughout; retire it stage by stage.
