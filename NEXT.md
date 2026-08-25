# NEXT — the morning checklist

> **Status after Aug 24, evening session (working credentials in hand):
> steps 1–4 are DONE except email.** The bridge ran END TO END against the
> real world tonight, several times:
>
> - Logged into the real IrroCloud as Jacob, mapped all 22 devices, and
>   confirmed the four field mappings (see `discovery/REPORT.md`).
> - Back-filled the ENTIRE season — ~40,000 readings per field, newest
>   within the hour — and rebuilt `reports/season-replay.html` from real
>   data. Re-running stores 0 new rows: dedupe proven live.
> - Ran the full morning pipeline with live Helios: 4/4 fields fetched,
>   analyzed, weather from Open-Meteo (works from this sandbox after all),
>   4/4 live forecasts, and 4/4 runs SAVED into Jacob's Helios history
>   (`bridge-field-N-2026-08-24` — visible in his app). The run shape was
>   read from his real history and a same-day re-run overwrites instead of
>   duplicating (verified).
> - Jacob's morning email, built from his real sensors, is waiting as a
>   dry-run in `outbox/` — worth reading with coffee.
>
> **What still stands between here and "runs itself every morning":**
> 1. **Gmail app password** (step 1) — email is the only untested stage;
>    everything goes to `outbox/` files until this exists.
> 2. **Merge + secrets on GitHub** (steps 5–6, best done together).
> 3. **Jacob's trigger numbers** — placeholder 50 cb until he replies.
> 4. **The Jacob switch** (step 7) — his written OK is recorded (Aug 24);
>    flip after one good dry-run morning on GitHub.

Henry: do these in order. The fastest way through is to open a Claude Code
session on this repository and say **"read NEXT.md, then do it"**.

---

## 1. Values — all in `.env` except:

- **The Gmail address to send from, and its app password** — STILL NEEDED.
  Click path: Google Account → Security → 2-Step Verification on → App
  passwords → create one named "helios bridge" → copy the 16 characters.
  Put them in `.env` as SMTP_USER / SMTP_PASSWORD.
- **Jacob's trigger number** — STILL NEEDED (per field if different); until
  then every email says "placeholder trigger 50 cb — reply with your
  number". Set in `fields.json` (`trigger_cb`).

Everything else is in and PROVEN: IrroCloud login, Jacob's Helios login
(same email/password works on both), Henry's email/phone, the 6:00 am
schedule, and Jacob's written OK (recorded Aug 24).

## 2. ~~Map the real IrroCloud site~~ DONE (Aug 24)

`discovery/REPORT.md` lists all 22 devices with their ids. The four fields
are mapped in `fields.json` (name + numeric device id + Helios field key).
Two findings that changed the plan, both handled:

- **The handline sensors are separate devices** (`SB Bennett 1 N HLS`,
  `SB Whitted SS`), not probe letters — the bridge doesn't fetch them, so
  every probe on the pivot devices counts as pivot. No probe-role question
  remains.
- **Exports are UTC** with explicit `+00:00` offsets; the parser reads them
  correctly as-is and emails render in Boise time. `TIMEZONE` stays
  `America/Boise`.
- The fetcher now uses the site's own CSV endpoint (`csv?&id=<device id>`,
  full history in one request) instead of clicking through pages — fewer
  moving parts; the click path remains as fallback. Re-running
  `python3 -m bridge.cli discover` works any time the site changes.

## 3. ~~Back-fill the season~~ DONE (Aug 24)

Full history stored (the CSV endpoint returns everything, so this reaches
well past July 1): Cunningham 6 42,168 readings; RV80 39,098; Bennett 1 N
39,607; Whitted 40,270 — all with newest timestamps minutes old.
`reports/season-replay.html` is rebuilt from the real season.

## 4. First live run — DONE except email (Aug 24)

Ran repeatedly with `FETCHER=browser`, `HELIOS_MODE=live`,
`NOTIFY_MODE=file`, `SEND_TO_JACOB=false`:

- 4/4 fetched + analyzed; Open-Meteo weather 4/4 (and Helios's own NOAA
  source failed server-side, so the bridge's caller-supplied weather
  fallback carried the forecasts — worth mentioning to Marco).
- 4/4 live forecasts; **4/4 saved into Jacob's Helios history** with
  deterministic ids (`bridge-field-N-<date>`); re-runs overwrite.
- Occasional 502s from Helios on individual fields degrade honestly
  ("unavailable this morning") and clear on the next run.
- Jacob's email + Henry's status email are in `outbox/` as files.

Remaining here once the Gmail app password exists:
`python3 -m bridge.cli test-email`, then set `NOTIFY_MODE=smtp` in `.env`
and `python3 -m bridge.cli run --force` — read the real status email in
your inbox. (Run this somewhere that can reach Gmail: GitHub Actions or
your own machine — NOT the Claude sandbox, which blocks SMTP.)

## 5. Merge, so the schedule can exist

GitHub only runs schedules from the **main** branch. Create the pull
request from the Claude Code screen for branch
`claude/irrocloud-helios-bridge-qnxh1o`, then merge it on GitHub. Best done
together with step 6 — a merged schedule with no secrets sends red runs
every morning.

## 6. Secrets, then one manual run on GitHub

- Add the secrets: repository → **Settings → Secrets and variables →
  Actions → New repository secret**, one per `.env` line except the three
  mode switches (the README lists the exact names). Shortcut if `gh` is
  signed in: `./scripts/push-secrets.sh`.
- Then: **Actions** tab → **Morning bridge run** → **Run workflow**. Confirm
  it goes green and the status email arrives. From tomorrow it runs itself
  at 5:15 am Boise time (email waiting by 6:00).

## 7. The Jacob switch

Jacob's written OK is in hand (recorded Aug 24). Once step 6's run is green
and you have read at least one `[DRY RUN → Jacob]` email in your inbox:
change the **SEND_TO_JACOB** secret on GitHub to `true` (and in `.env` if
you also run it by hand). Until then he receives nothing.

## 8. Verified / still open

- **Helios API shapes** — verified against the live `/openapi.json`
  (committed as `docs/helios-openapi-2026-08-24.json`); eight divergences
  fixed, then proven by real accepted requests. The **run-save shape** was
  read from Jacob's actual history and is fully implemented; the
  template-match guard stays on, so a frontend reshape re-triggers a safe
  skip rather than a bad write. Still Appendix-A provenance (harmless now,
  all covered by live behavior): HELIOS's internal parser constants and
  `MAX_PHYSICAL_SENSOR_COUNT`.
- **Open-Meteo works** — from this sandbox and (untested but open-internet)
  GitHub Actions. Helios's own NOAA enrichment is currently failing
  server-side; the bridge's fallback covers it, but Marco may want to know.
- **Email** — the one never-exercised stage. Step 4's remainder covers it.
- **Claude-sandbox notes** — the code finds the sandbox's Chromium and
  egress proxy on its own (`bridge/fetchers/_launch.py`; inert elsewhere).
  SMTP is blocked in the sandbox; everything else now works from it.
- **Repo size, worth watching** — the CSV endpoint returns each device's
  FULL history, so `data/` is ~50 MB (mostly the 40 MB SQLite store) and
  the morning workflow commits it back daily. If the repository grows
  uncomfortably after a few weeks, ask a session to trim old raw exports
  and/or stop committing the sqlite (the readings CSVs alone can rebuild
  it) — a one-line workflow change.
- **November** — remember the daylight-saving line in `morning.yml`.
- **DECISIONS.md** — the timezone and schema VERIFY items are settled;
  what remains open there is only the trigger placeholder.
