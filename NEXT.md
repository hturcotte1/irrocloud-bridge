# NEXT — the morning checklist

> **Found on the morning of Aug 24:** this Claude Code cloud environment's
> network policy blocks ALL the live services the bridge needs (IrroCloud,
> Helios, the weather service, Gmail — and the HELIOS snapshot). Steps 2–4
> cannot run from this sandbox until that changes. The fix is a one-time
> settings change: on claude.ai/code, open this repository's **environment
> settings → network access** and either allow full network access or add
> these to the allowed list: `www.irrocloud.com`,
> `irrigant-helios.up.railway.app`, `api.open-meteo.com`,
> `archive-api.open-meteo.com`, `smtp.gmail.com`, `api.github.com`.
> Then start a fresh session (the running machine keeps the old policy) and
> say "read NEXT.md, then do it" again. Fallback if the policy cannot
> change: run the live steps through GitHub Actions instead (it has open
> internet) — do step 6's secrets first, then run the workflow from the
> branch via the Actions tab.

Henry: do these in order. The fastest way through is to open a Claude Code
session on this repository and say **"read NEXT.md, then do it"** — it will
ask you for the values in step 1 and drive the rest, pausing where only you
can click or decide. Nothing has been sent to anyone overnight, and nothing
will be sent to Jacob until step 7.

---

## 1. Have these values ready (the session will ask for them)

One per line — what it is and where it comes from:

- **Helios web address** — the https://… address where the Helios app lives
  (the one Jacob logs into; Marco knows it if you don't).
- **Jacob's Helios login** — his email and password for Helios, because the
  bridge saves each morning's run into *his* history.
- **IrroCloud login** — the email and password Jacob (or you) uses at
  irrocloud.com, where his soil sensors report.
- **The Gmail address to send from, and its app password** — an "app
  password" is a special 16-character password Google issues so a program
  can send mail without knowing your real password. Click path: Google
  Account → Security → turn on 2-Step Verification if it isn't already →
  App passwords → create one named "helios bridge" → copy the 16 characters.
- **Your email** — where the daily status report (and, for now, Jacob's
  dry-run emails) go.
- **Jacob's email** — where his morning email will go once you flip the
  switch in step 7.
- **Your phone number** — shown at the bottom of Jacob's email so he can
  call or text you.
- **What time Jacob starts his morning** — the schedule is currently set so
  his email is waiting by 6:00 am; if that's wrong we move it.
- **Jacob's trigger number** — the tension (centibars) at which he likes to
  irrigate, per field if he has different ones. If you don't have it yet,
  say "placeholder" and every email will keep saying "placeholder trigger
  50 cb — reply with your number" until he does.
- **Has Jacob given written OK for automated access to his IrroCloud
  account?** Yes or not yet. Step 7 waits on this.

Where they go: the session copies `.env.example` to `.env` and fills it in.
Check with: `python3 -m bridge.cli setup-check`.

## 2. Map the real IrroCloud site

Run: `python3 -m bridge.cli discover`

This signs in (read-only), screenshots every page, tries one small export,
and writes `discovery/REPORT.md`. Then, with that report:

- Confirm which IrroCloud device belongs to which field, and put each exact
  device name into `fields.json` (`irrocloud_device_name`).
- Identify which probe letter (a/b/c) is the **handline** probe on
  **Bennett 1 N** (the one called "HLS") and on **Whitted** (the one called
  "SS"), and set those roles in `fields.json`. Until then all probes count
  toward the headline numbers.
- Confirm the export's timezone (the report gathers the evidence; if exports
  turn out to be UTC, set `TIMEZONE=UTC` in `.env`).
- If any discovery step failed, the session adjusts the SELECTORS block at
  the top of `bridge/fetchers/irrocloud_browser.py` — that block is the only
  site-specific part.
- If the login shows a code-by-text step or a captcha, a human does it once
  in a normal browser first, then re-run discover.

## 3. Back-fill the season

Run: `python3 -m bridge.cli fetch --days 60`

(Helios's stored data ends July 8, so this reaches back to July 1 with
margin.) It prints, per field, how many readings were stored and the newest
timestamp — sanity-check those against what the IrroCloud site shows. Then
`python3 -m bridge.cli replay` rebuilds `reports/season-replay.html` from
real data — worth a look before Jacob ever sees anything.

## 4. First live run (still a dry run for Jacob)

- `python3 -m bridge.cli test-email` — confirm a test message lands in your
  inbox (this exercises the Gmail app password).
- Set in `.env`: `FETCHER=browser`, `HELIOS_MODE=live`, `NOTIFY_MODE=smtp`,
  `SEND_TO_JACOB=false`, then: `python3 -m bridge.cli run`
- Read the status email it sends you, including Jacob's email as it would go
  out.
- **Confirm the run shows up in Jacob's Helios history.** Note: the first
  live attempt will probably SKIP the save on purpose — the exact "run"
  shape could not be confirmed overnight (see item 6 below); the session
  fixes that here by reading the newest entry from Jacob's history
  (`GET /web/runs`) and extending `build_run_object` in `bridge/helios.py`
  to match, then re-running (a re-run overwrites, it never duplicates).

## 5. Merge, so the schedule can exist

GitHub only runs schedules from the **main** branch. Create the pull request
from the Claude Code screen for branch
`claude/irrocloud-helios-bridge-qnxh1o`, then merge it on GitHub.

## 6. Secrets, then one manual run on GitHub

- Add the secrets: repository → **Settings → Secrets and variables →
  Actions → New repository secret**, one per `.env` line except the three
  mode switches (the README lists the exact names). Shortcut if `gh` is
  signed in: `./scripts/push-secrets.sh`.
- Then: **Actions** tab → **Morning bridge run** → **Run workflow**. Confirm
  it goes green and the status email arrives. From tomorrow it runs itself
  at 5:15 am Boise time.

## 7. The Jacob switch — only with his written OK in hand

When Jacob's written consent for automated access exists: change the
**SEND_TO_JACOB** secret on GitHub to `true` (and in `.env` if you also run
it by hand). Until then he receives nothing; you receive his emails marked
`[DRY RUN → Jacob]`.

## 8. What could not be done or verified overnight

Each of these is why, and what to do:

- **The HELIOS reference snapshot would not download** — the sandbox's
  network proxy refused it (HTTP 403). Everything built from its Appendix-A
  stand-in works, but the morning session should download the snapshot and:
  verify the parser rules and constants (docs/HELIOS-NOTES.md lists them),
  replace the reconstructed schema models in `bridge/_helios_schemas/` with
  verbatim copies, read `mapApiRun` and extend `build_run_object`
  (step 4 above), and check the real values of `MAX_PHYSICAL_SENSOR_COUNT`
  and the browser's `water_rights_schedule`.
- **Open-Meteo (the free weather service) was unreachable from the sandbox**,
  so the live weather path is untested — the run degrades to "rain forecast
  unavailable" (that path IS tested). The first live run confirms weather
  works.
- **Real email sending was never exercised** (no credentials overnight; file
  mode was used). Step 4's test-email covers it.
- **The real IrroCloud site has never been touched.** All browser code was
  proven against a built-in fake site only; step 2 is where reality arrives.
- **If Playwright's browser is missing** on whatever machine runs this
  (`playwright install --with-deps chromium` fixes it; the GitHub workflow
  already does this itself). In the Claude Code cloud sandbox, the browser
  tests ran using the machine's pre-installed Chromium.
- **The schedule time assumed Jacob wants email by 6:00 am** — confirm in
  step 1 and adjust the cron line in `.github/workflows/morning.yml` if not.
- **DECISIONS.md** lists every other default chosen overnight (timezone
  handling, the primary-series rule, the placeholder trigger, …) — worth a
  skim with coffee; anything marked VERIFY has a step above.
