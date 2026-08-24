# NEXT — the morning checklist

> **Status after the Aug 24 morning session** (values from Henry's step 1
> applied): the network is partly unblocked and a lot got done — but the
> session hit one hard blocker only a human can clear:
>
> **The IrroCloud login was rejected.** The site quietly re-shows its login
> form for the credentials Henry supplied (verified two independent ways;
> the site shows no error text, so it looks identical to a typo). Nothing is
> wrong with the browser automation — it reaches the real login page and
> posts correctly. The session deliberately did NOT try password variations —
> guessing could lock Jacob's account.
>
> **Second attempt, later on Aug 24:** Henry supplied
> `jacobbriscoe21@gmail.com` / the same password for BOTH IrroCloud and
> Helios. **Both services reject it.** IrroCloud silently re-shows the form
> (for this email and for `jbriscoe@gmail.com` alike), and Helios answers in
> plain words: `"Invalid email or password."` for either email. Two
> independent systems rejecting the same password points at the password —
> or, for Helios, at an account that does not exist yet (ask Marco whether
> Jacob was ever registered). **The reliable fix: have Jacob log into
> irrocloud.com AND the Helios page himself in a normal browser, and write
> down exactly what worked** — exact email, exact capitalization. IrroCloud
> also has a "Recover Password" link he can use.
>
> Done this morning (details in the sections below):
> - `.env` is filled in with everything Henry has; `setup-check` passes for
>   the current safe modes.
> - The browser stack now works inside the Claude sandbox (its egress proxy
>   needed three accommodations, all committed and inert elsewhere), and it
>   reaches the real IrroCloud login page — selectors confirmed right.
> - The reconstructed Helios schemas were **verified against the live
>   service's own OpenAPI** (committed as
>   `docs/helios-openapi-2026-08-24.json`). Eight real divergences were found
>   and fixed — the worst meant **every live forecast call would have been
>   rejected (HTTP 422)**. See `bridge/_helios_schemas/PROVENANCE.md`.
> - Jacob's **written OK for automated access: Henry says it is in hand**
>   (recorded Aug 24). Step 7 still waits for email to exist and dry-runs to
>   be reviewed.
>
> Still missing (the session will ask again):
> - the **corrected IrroCloud password** — blocks steps 2, 3, 4;
> - **Jacob's Helios login** — blocks the live Helios part of step 4;
> - the **Gmail address + app password** — blocks test-email and step 6;
> - **Jacob's trigger numbers** — emails keep saying "placeholder trigger
>   50 cb — reply with your number" until then.
>
> Sandbox notes for the next session: Open-Meteo and Gmail SMTP are still
> unreachable from this sandbox (weather degrades gracefully; email stays in
> file mode) — both work from GitHub Actions, which has open internet. The
> browser needs no special setup any more; the code finds the sandbox's
> Chromium and proxy on its own.

Henry: do these in order. The fastest way through is to open a Claude Code
session on this repository and say **"read NEXT.md, then do it"** — it will
ask you for the values in step 1 and drive the rest, pausing where only you
can click or decide. Nothing has been sent to anyone, and nothing will be
sent to Jacob until step 7.

---

## 1. Have these values ready (the session will ask for them)

Filled in on Aug 24 where Henry had them (in `.env`, which stays on this
machine and is never committed):

- **Helios web address** — DONE: `https://irrigant-helios.up.railway.app`
  (reachable, version 0.5.0, healthy).
- **Jacob's Helios login** — STILL NEEDED. The bridge saves each morning's
  run into *his* history; without it Helios stays in offline mode.
- **IrroCloud login** — NEEDS RE-CHECKING: the site rejects
  `jbriscoe@gmail.com` with the password supplied on Aug 24. Confirm both
  with Jacob, exactly as typed.
- **The Gmail address to send from, and its app password** — STILL NEEDED.
  Click path: Google Account → Security → 2-Step Verification on → App
  passwords → create one named "helios bridge" → copy the 16 characters.
- **Your email** — DONE: `henrylachtur@gmail.com`.
- **Jacob's email** — DONE: `jbriscoe@gmail.com`.
- **Your phone** — DONE: `208-994-8295` (goes at the bottom of Jacob's email).
- **What time Jacob starts his morning** — DONE: 6:00 am confirmed; the
  5:15 am schedule in `.github/workflows/morning.yml` already fits. No change.
- **Jacob's trigger number** — STILL NEEDED (per field if different); until
  then every email says "placeholder trigger 50 cb — reply with your number".
- **Jacob's written OK for automated access** — DONE: yes, per Henry, Aug 24.

Check with: `python3 -m bridge.cli setup-check`.

## 2. Map the real IrroCloud site — BLOCKED on the corrected login

Once the corrected password is in `.env`, run:
`python3 -m bridge.cli discover`

The Aug 24 attempt got as far as the real login form (see
`discovery/REPORT.md` and `discovery/02-login-failed.png` locally): the page
matches the existing selectors, so with a working password this should run
clean. Then, with the report:

- Confirm which IrroCloud device belongs to which field, and put each exact
  device name into `fields.json` (`irrocloud_device_name`).
- Identify which probe letter (a/b/c) is the **handline** probe on
  **Bennett 1 N** (called "HLS") and on **Whitted** (called "SS"), and set
  those roles in `fields.json`. Until then all probes count toward the
  headline numbers.
- Confirm the export's timezone (if exports turn out to be UTC, set
  `TIMEZONE=UTC` in `.env`).
- If any discovery step failed, adjust the SELECTORS block at the top of
  `bridge/fetchers/irrocloud_browser.py` — that block is the only
  site-specific part.
- If the login shows a code-by-text step or a captcha, a human does it once
  in a normal browser first, then re-run discover.

## 3. Back-fill the season — after step 2

Run: `python3 -m bridge.cli fetch --days 60`

(Helios's stored data ends July 8, so this reaches back to July 1 with
margin.) It prints, per field, how many readings were stored and the newest
timestamp — sanity-check those against what the IrroCloud site shows. Then
`python3 -m bridge.cli replay` rebuilds `reports/season-replay.html` from
real data — worth a look before Jacob ever sees anything.

## 4. First live run (still a dry run for Jacob)

- `python3 -m bridge.cli test-email` — needs the Gmail app password, and
  must run somewhere that can reach Gmail (GitHub Actions, or Henry's own
  machine — NOT the Claude sandbox, which blocks SMTP).
- Set in `.env`: `FETCHER=browser`, `HELIOS_MODE=live` (needs Jacob's Helios
  login), `NOTIFY_MODE=smtp`, `SEND_TO_JACOB=false`, then:
  `python3 -m bridge.cli run`
- Read the status email it sends you, including Jacob's email as it would go
  out.
- **Confirm the run shows up in Jacob's Helios history.** The first live
  attempt will probably SKIP the save on purpose: the exact "run" shape
  could not be confirmed (the server accepts any object — the shape
  discipline is the Helios frontend's, verified Aug 24), so the bridge
  templates it from the newest entry in Jacob's history (`GET /web/runs`)
  and refuses to save until the shapes match. The session fixes that here by
  reading his history and extending `build_run_object` in `bridge/helios.py`
  to match, then re-running (a re-run overwrites, never duplicates).
  Jacob's history being EMPTY also skips the save — then someone must do one
  run in the Helios web app first, or Marco supplies `mapApiRun` from
  `src/api/run-builders.js`.

## 5. Merge, so the schedule can exist

GitHub only runs schedules from the **main** branch. Create the pull request
from the Claude Code screen for branch
`claude/irrocloud-helios-bridge-qnxh1o`, then merge it on GitHub. (Best
done together with step 6 — a merged schedule with no secrets sends red
runs every morning.)

## 6. Secrets, then one manual run on GitHub

- Add the secrets: repository → **Settings → Secrets and variables →
  Actions → New repository secret**, one per `.env` line except the three
  mode switches (the README lists the exact names). Shortcut if `gh` is
  signed in: `./scripts/push-secrets.sh`.
- Then: **Actions** tab → **Morning bridge run** → **Run workflow**. Confirm
  it goes green and the status email arrives. From tomorrow it runs itself
  at 5:15 am Boise time.

## 7. The Jacob switch

Jacob's written OK is in hand (per Henry, Aug 24). Once steps 4–6 look
right — Henry has read at least one `[DRY RUN → Jacob]` email and the run is
green on GitHub — change the **SEND_TO_JACOB** secret on GitHub to `true`
(and in `.env` if you also run it by hand). Until then he receives nothing;
you receive his emails marked `[DRY RUN → Jacob]`.

## 8. Verified / still open (was: what could not be done overnight)

- **HELIOS reference snapshot** — the repo itself is still inaccessible (no
  accessible GitHub repository holds the HELIOS app source; the tarball 403
  was never about network alone). DONE INSTEAD (Aug 24): all **API shapes**
  verified against the live service's `/openapi.json` and corrected — see
  `bridge/_helios_schemas/PROVENANCE.md` for the eight real fixes, including
  one that would have 422-failed every live forecast. STILL OPEN: the
  server-internal facts (parser rules, constants, `MAX_PHYSICAL_SENSOR_COUNT`,
  `mapApiRun`) — need Marco to share the source; the bridge's conservative
  caps and template-matched saves cover the gap safely.
- **Open-Meteo** — still unreachable from the Claude sandbox; the run
  degrades to "rain forecast unavailable" (tested path). Works from GitHub
  Actions; the first live run there confirms it.
- **Real email sending** — still never exercised (no credentials, and the
  sandbox blocks SMTP). Step 4's test-email covers it, from GitHub Actions
  or Henry's machine.
- **The real IrroCloud site** — REACHED (Aug 24): login page loads, form
  matches the selectors. Only the credential rejection stands between here
  and step 2.
- **Claude-sandbox browser setup** — DONE (Aug 24): the code now finds the
  pre-installed Chromium and the sandbox's egress proxy on its own
  (`bridge/fetchers/_launch.py`; inert on GitHub Actions and normal
  machines). Elsewhere, `playwright install --with-deps chromium` still
  applies.
- **The 6:00 am schedule** — CONFIRMED (Aug 24, step 1). Remember the
  November daylight-saving line in `morning.yml`.
- **DECISIONS.md** — updated Aug 24; the schema VERIFY item is done, the
  parser/timezone items still wait on a real export (step 2).
