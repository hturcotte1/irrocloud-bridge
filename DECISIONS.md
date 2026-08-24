# Decisions made overnight (2026-08-24), and why

Henry: these are the calls I made on your behalf while you slept, so you or
Marco can review them. Anything marked **VERIFY** has a matching step in
NEXT.md. "The brief" is the build document this repo was built from.

## Source material

1. **The HELIOS reference snapshot could not be downloaded** — the sandbox's
   network proxy refused the GitHub request (HTTP 403). Everything that was
   supposed to come from reading the snapshot (parser rules, schemas, API
   shapes) was instead built from Appendix A of the brief, which was extracted
   from the same snapshot the day before. **VERIFY** in the morning; the
   token was used for that one download attempt and nothing else.
2. **The schema models are reconstructed, not copied**
   (`bridge/_helios_schemas/`, see its PROVENANCE.md). Request models reject
   unknown keys (a typo in our payload should fail loudly); response models
   accept extra keys (the live server may return more than Appendix A
   listed). **VERIFY**: replace with verbatim copies from the snapshot.

## Parsing and time

3. **Export timestamps with no timezone are treated as Boise local time**
   (`America/Boise`). HELIOS's own parser assumed UTC; for a grower-facing
   morning email, local time is the safer default, and the discovery run
   collects evidence to settle it. **VERIFY** against a real export. The two
   odd daylight-saving hours a year (one skipped, one doubled) are dropped
   rather than guessed.
4. **254-sentinel and negative readings are dropped entirely, not stored.**
   The run log records how many were dropped. Readings above 240 are stored
   as 240 with the flag `clipped` (matching HELIOS's clip rule), and that
   flag travels all the way into the Helios payload.
5. **Duplicate timestamps keep the last row** — same as HELIOS.

## Analysis

6. **The primary-series rule mirrors HELIOS exactly**, including a subtle
   consequence: when the healthy probes read identically, the MAD is 0 and
   the outlier filter deliberately does not run, so a stuck probe can lead
   for that timestamp. I kept the quirk rather than "improving" it, so the
   bridge never disagrees with Helios about the same data.
7. **Probes with an unconfirmed role (`REPLACE_ME` in fields.json) count as
   pivot probes** until a human marks them `handline`. Excluding an unknown
   probe would silently change the headline numbers on Bennett 1 N and
   Whitted; including it matches what Helios itself would do. **VERIFY**:
   the morning discovery identifies which letter is the handline probe.
8. **The 18-inch depth is mentioned only when it tells a surprising story**:
   it is at/past the trigger when the 12-inch is not, or it will get there at
   least a full day sooner. The deep sensor lagging the shallow one is the
   normal story and would be noise in every email.
9. **Days-to-trigger beyond seven days is reported as "more than a week out
   at this rate"** — a weekday name ten days ahead reads as false precision.
   Parts of day: before 5 am "overnight", then morning / afternoon / evening.
10. **The placeholder trigger is 50 cb** (the brief's number), flagged on
    every field that uses it, in every email, until Jacob supplies his own.

## Forecast bookkeeping (the scorecard)

11. Three forecast sources are recorded every run: `helios_model` (the model
    line), `persistence` ("tomorrow looks like today" — from Helios's own
    baseline, or today's primary reading if Helios was down), and `dry_down`
    (today's reading plus the measured rate, i.e. the email's "at this rate"
    arithmetic). A same-day re-run updates rather than duplicates.
12. A forecast is graded when a reading exists within ±90 minutes of its
    target time; the email's footer quotes the 14-day average error only
    once at least three graded points exist.

## Talking to Helios

13. **`water_rights_schedule` is sent as `["unrestricted"]`** — the payload
    requires at least one entry and Appendix A did not show what the Helios
    browser sends. **VERIFY** against `buildPredictionRequest()`.
14. **At most the newest 500 readings go into one prediction request.** The
    real cap (`MAX_PHYSICAL_SENSOR_COUNT`) was not in Appendix A. **VERIFY**.
15. **Saving a run into Jacob's history is template-confirmed**: before
    posting, the bridge fetches his run history and compares shapes; on any
    mismatch it SKIPS the save and says why, because writing a guessed shape
    into his history is worse than not saving. Appendix A only listed the
    beginning of the run object, so tonight the save will skip until the
    morning session extends `build_run_object` from the snapshot's
    `mapApiRun` (or the history template). Run ids are deterministic
    (`bridge-<field>-<date>`) so a re-run overwrites, never duplicates.
16. **Helios's water/wait decision never appears in Jacob's email** — it
    would read as an irrigation instruction, which the brief forbids. It
    appears only in Henry's status email. Tests enforce this and the absence
    of the word "recommend".

## Delivery

17. **The Jacob-resend guard is per calendar day and applies only to Jacob's
    email**; Henry's status email always goes out. `run --force` overrides.
18. A note about unverified irrigation ("we could not check rain records")
    appears only when the wetting was recent (within 3 days) — an old
    unverified label would be noise — and once for all fields, not per field.

## Operations

19. **The "commit data/ back" step lives in the GitHub workflow, not in the
    Python program.** GitHub's computers are wiped after every run, so the
    repository itself is the bridge's memory — readings, forecasts, and the
    sent-log (data/bridge.sqlite) are committed back even when the run
    failed. Locally, `run` never touches git.
20. The three mode switches are pinned inside the workflow (`FETCHER=browser`,
    `HELIOS_MODE=live`, `NOTIFY_MODE=smtp`) so no stray file can flip them;
    `SEND_TO_JACOB` alone comes from a secret so Henry can flip it on the
    website without touching code.
21. The schedule is 11:15 UTC = 5:15 am Boise **daylight** time. GitHub crons
    cannot follow DST; `morning.yml` documents the one-line November change.
22. **Discovery artifacts are gitignored except REPORT.md** — screenshots and
    saved HTML of a logged-in site can contain account details. The network
    log scrubs the login request body.
23. The built-in sample data (fixture mode) shifts its timestamps forward
    when they have gone stale, so an offline demo months from now still looks
    like a live morning; when the data is fresh (and in tests), nothing is
    shifted.
24. One `requirements.txt` includes the test tools — a throwaway repo does
    not need a split dependency setup.

## Could not be verified tonight (all in NEXT.md)

- The real IrroCloud page layout, selectors, export format, and timezone
  (discovery was exercised against a built-in fake site only).
- Open-Meteo weather (the sandbox blocked it; the run degrades gracefully —
  that path is what tonight's offline run exercised).
- Real SMTP sending (no credentials tonight; file mode was used).
- Everything marked **VERIFY** above, against the real HELIOS snapshot.
