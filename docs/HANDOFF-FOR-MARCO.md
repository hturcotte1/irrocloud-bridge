# Hand-off notes for Marco

This bridge is a deliberate throwaway: a proof of concept that gets Jacob
Briscoe's IrroCloud sensor data into Helios every morning while the season
still matters. It is built to be replaced by a real ingest path inside
Helios. This document says how it works, what it borrows from HELIOS, and
what a proper replacement needs.

## How it works (one paragraph)

A Python CLI (`bridge/cli.py::cmd_run`) runs each morning on GitHub Actions:
a Playwright fetcher logs into IrroCloud with Jacob's credentials and exports
one CSV per field (`bridge/fetchers/irrocloud_browser.py`); a port of your
`load_irrocloud_csv` cleans it (`bridge/parse.py`); readings land in SQLite
(`data/bridge.sqlite`, committed back to the repo because Actions runners are
ephemeral — the repo is the persistence layer); `bridge/analyze.py` mirrors
your primary-sensor selection and wetting detection and adds dry-down slope /
days-to-trigger arithmetic; `bridge/helios.py` logs into the Helios web API
as Jacob, POSTs `/web/predict` per field, and saves the run into his history
via `POST /web/runs`; `bridge/message.py` composes the grower email (no
imperatives, model output always labeled pilot + review-gated) and Henry's
status email.

## What it borrows from HELIOS — verify me

The snapshot download was blocked the night this was built, so the following
were **reconstructed from a day-old extract (Appendix A of the build brief)**
rather than copied. Please diff against the real thing:

| Bridge file | Mirrors | Notes |
|---|---|---|
| `bridge/parse.py` | `helios/scripts/parse_irrocloud_data.py::load_irrocloud_csv` | one deliberate difference: naive timestamps treated as America/Boise, not UTC |
| `bridge/analyze.py` | `_select_primary_sensor`, `detect_wetting_events`, constants | includes the MAD==0 no-filter quirk, on purpose |
| `bridge/_helios_schemas/` | `helios/schemas/inputs.py`, `outputs.py` | reconstructed pydantic; see its PROVENANCE.md |
| `bridge/helios.py::build_run_object` | `src/api/run-builders.js::mapApiRun` | only the documented prefix; the live save path refuses to POST unless the shape exactly matches the newest `run_history` entry, so nothing malformed can land in Jacob's history |

Two payload guesses to check against `buildPredictionRequest()`:
`water_rights_schedule=["unrestricted"]`, and a conservative cap of 500
readings per request (`MAX_PHYSICAL_SENSOR_COUNT` wasn't in the extract).

## What a real ingest path needs (to make this repo deletable)

1. **A first-class ingest endpoint** — accept batches of
   `(field_id, sensor_id, timestamp, tension_cb, quality_flag, source)` with
   an idempotency key per batch; dedupe on `(sensor_id, timestamp)` server
   side. The bridge's tidy rows are exactly this shape already.
2. **A sensor registry per field** — probe letter, depth, and role
   (pivot vs handline) so primary-series selection can exclude handline
   probes server-side. Today that knowledge lives in this repo's
   `fields.json` and nowhere in Helios.
3. **Credential custody** — either Irrometer's official API (Irrigant has
   asked; `bridge/fetchers/irrocloud_api.py` is the stub) or a proper vault
   for grower credentials. Today Jacob's IrroCloud login sits in GitHub
   Actions secrets, which is acceptable for a pilot bridge and not for a
   product; the terms-of-service question is also still open, which is why
   every document calls this a temporary bridge.
4. **A scheduled fetch worker inside Helios** with the bridge's operational
   rules: fetch each field once per run, never poll, stop-and-alert on
   anything unexpected (2FA, layout change, unknown CSV layout, zero rows).
5. **A stable run-save contract** — `POST /web/runs` currently takes the
   browser's client-built object; an API caller needs a versioned shape (or
   server-side construction from a prediction id). The bridge's
   template-confirmation dance exists only because of this gap.
6. **Server-side scorecard** — the bridge grades every forecast against the
   next day's readings (model vs persistence vs plain dry-down arithmetic,
   14-day MAE) in its own SQLite. That belongs in Helios; it is exactly the
   evidence the review gate wants.

## Known fragilities (ranked)

1. **Screen-scraping.** Selectors live in one marked block at the top of
   `irrocloud_browser.py`; any IrroCloud redesign breaks the fetch (loudly —
   screenshot + alert email, never silent). The `discover` command re-maps
   the site.
2. **Timezone assumption** until the morning discovery confirms it.
3. **GitHub Actions as scheduler and the repo as database.** Fine for one
   grower and one season; wrong for two of either. No DST awareness (cron
   line changes by hand in November).
4. **Reconstructed schemas** — drift between them and the live server fails
   validation loudly, which is the intended failure mode, but it fails.
5. **The review gate is surfaced, never bypassed** — `operator_review_required`
   and the quarantine sentence flow into Jacob's email footer by design. If
   the gate's wording changes, `bridge/message.py` and its golden tests are
   the places that care.
