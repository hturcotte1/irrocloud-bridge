# IrroCloud → Helios bridge

Every morning, this tool signs into IrroCloud (the website where Jacob's soil
sensors report), downloads the last week of readings for his four fields,
cleans them up, does the simple arithmetic (how fast each field is drying, and
when it will reach his trigger number), asks Helios for its forecast, saves
that run into Jacob's Helios history, and sends him a short plain-English
email. Zero effort from Jacob. Zero effort from Henry once it is running.

**This is a temporary bridge, not an integration.** It works by logging into
the IrroCloud website the way a person would. A proper connection inside
Helios will replace it (notes for Marco are in `docs/HANDOFF-FOR-MARCO.md`).

**Start here in the morning: open `NEXT.md`.** It is the ordered checklist of
everything left to do, and lists the handful of passwords and addresses to
have ready. The fastest path: open a Claude Code session on this repository
and say *"read NEXT.md, then do it"* — it will ask for the values and do the
steps.

---

## What "safe by default" means here

Fresh out of the box, with nothing configured, the bridge runs entirely on
built-in sample data: it touches no websites and sends no email (messages are
written into the `outbox/` folder as text files instead). Three switches in
the settings file turn on the real thing, one at a time. And even with
everything live, **nothing goes to Jacob** until one line is changed — see
"The Jacob switch" below.

## One-time setup

1. Make a copy of the file `.env.example` and name the copy exactly `.env`
   (yes, starting with a dot). That file is where all settings and passwords
   live. It stays on this computer only — it is never uploaded.
2. Replace every `REPLACE_ME` in `.env`. Each line has a comment saying what
   it is; `NEXT.md` lists where each value comes from.
3. Check it: run `python3 -m bridge.cli setup-check` in the terminal (the
   window where you type commands). It says, in plain English, what is filled
   in and what is still missing.

## Running it once by hand

In the terminal, from this folder:

    python3 -m bridge.cli run

That is the entire morning routine, once. When it finishes it prints a short
table — one line per step, each marked OK, WARN, FAIL, or SKIP. To see the
same summary later: `python3 -m bridge.cli status`.

## How to tell whether it worked

After every run, a **status email** goes to Henry (or, before email is set
up, a file appears in `outbox/`). Its subject line says everything important
at a glance:

    Bridge OK — 2026-08-25 — 4/4 fields — through 6:02 am

- **OK** — everything worked.
- **WARN** — the morning email went out, but something was off (one field
  missing, the rain forecast unreachable, a Helios save skipped). The body
  says exactly what, in the WARNINGS list.
- **FAIL** — the run could not do its job. The body says which step broke and
  attaches screenshots when the website was involved.

Below the warnings, the status email always shows **Jacob's email exactly as
it was sent** (or as it *would* have been sent, in dry-run mode), so you can
read what he read.

## The Jacob switch

While `SEND_TO_JACOB=false` (the way it ships), every email meant for Jacob
goes **to Henry instead**, with `[DRY RUN → Jacob]` at the front of the
subject. Jacob gets nothing.

To start sending to Jacob for real — only once his written OK for automated
access is in hand:

- For runs from this computer: open `.env` and change the line
  `SEND_TO_JACOB=false` to `SEND_TO_JACOB=true`.
- For the automatic morning runs on GitHub: change the **SEND_TO_JACOB**
  secret to `true` (same click path as "Adding the secrets" below).

Change it back to `false` any time to go back to dry runs.

## The automatic schedule

The file `.github/workflows/morning.yml` tells GitHub to run the bridge every
morning at 5:15 am Boise time on GitHub's computers (that early so the email
is waiting at 6:00). Two things must happen before the schedule works — both
are steps in `NEXT.md`:

1. **This branch must be merged.** GitHub only runs schedules from the main
   branch. Create the pull request from the Claude Code screen, then merge it
   on GitHub.
2. **The secrets must be added** (below), because GitHub's computers cannot
   read the `.env` file on this machine.

To run it by hand on GitHub: repository page → **Actions** tab → **Morning
bridge run** → **Run workflow** button. Green check = worked; red X = the
status email and the attached files say why.

**To pause it:** Actions tab → Morning bridge run → the "⋯" menu at the top
right → **Disable workflow**. (Enable workflow in the same place turns it
back on.)

**Note about November:** GitHub schedules do not understand daylight-saving
time. When the clocks change in early November, the run will arrive an hour
off until one line in `morning.yml` is changed — the file says exactly which
line, or ask Claude Code to do it.

## Adding the secrets on GitHub

A "secret" is GitHub's locked box for passwords, so the scheduled run can use
them without them ever appearing in the code. Click path, on the repository's
GitHub page:

**Settings → Secrets and variables → Actions → New repository secret**

Add one secret per line of `.env` (same name, same value), **except**
`FETCHER`, `HELIOS_MODE`, and `NOTIFY_MODE` — those three are fixed inside
the workflow on purpose. So: `SEND_TO_JACOB`, `IRROCLOUD_URL`,
`IRROCLOUD_EMAIL`, `IRROCLOUD_PASSWORD`, `HELIOS_URL`, `HELIOS_EMAIL`,
`HELIOS_PASSWORD`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
`HENRY_EMAIL`, `JACOB_EMAIL`, `HENRY_PHONE`, `TIMEZONE`.

Shortcut if the `gh` tool is signed in on this machine:
`./scripts/push-secrets.sh` reads `.env` and sets them all (it prints only
the names, never the values).

## When something breaks

Open a new Claude Code session **on this repository**, paste the status email
(or the red run's log from the Actions tab), and say **"fix this."** That is
the whole procedure. The bridge stops and alerts rather than guessing, so
"broken" almost always means the IrroCloud website changed a little and a
selector needs adjusting.

## Changing Jacob's trigger number

The trigger is the tension number (in centibars) at which Jacob likes to
irrigate. Until he gives one, the bridge uses a placeholder of 50 and says so
in every email. To set the real number: open `fields.json`, find the field,
and change `"trigger_cb": "REPLACE_ME"` to e.g. `"trigger_cb": 45`. Each
field can have its own number. (Or ask Claude Code: "set Jacob's trigger on
RV80 to 45".)

## When Irrometer sends API access

Open a Claude Code session on this repository and say: **"implement
`fetchers/irrocloud_api.py` from these docs"** — and paste the documentation
they sent. Only the fetching layer changes; everything else (cleaning,
analysis, emails, Helios) stays as it is. Then set `FETCHER=api` in `.env`
and in time delete the browser code.

## What is in this folder

| Where | What |
|---|---|
| `bridge/` | the program itself, in small pieces |
| `fields.json` | the four fields: names, locations, crops, trigger numbers |
| `data/` | every raw download, the cleaned history, and the little database |
| `outbox/` | emails written as files while email sending is off |
| `logs/` | what the last run did (`last-run.json`), failure screenshots |
| `discovery/` | what the `discover` command learned about the IrroCloud site |
| `reports/` | sample emails and the season-replay page (open in a browser) |
| `tests/` | more than a hundred automatic checks; `./scripts/check.sh` runs them all |
| `NEXT.md` | **the morning checklist** |
| `DECISIONS.md` | every default that was chosen overnight, and why |
| `docs/` | notes on Helios, and the hand-off document for Marco |
