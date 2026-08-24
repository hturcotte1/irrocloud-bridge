"""Compose the two emails: Jacob's morning note and Henry's status report.

Honesty rules (build brief §11) enforced here, not left to chance:

- No irrigation instructions and no "recommend": Helios's water/wait decision
  is NEVER rendered in Jacob's email (it would read as an instruction). It
  appears only in Henry's status email, labeled as pilot output.
- The model line is always prefixed "Helios pilot forecast", and because the
  model is quarantined (operator_review_required), the footer says the model
  is under review and its forecast is not something to irrigate on by itself.
- Every email carries "readings through <time>"; a stale field is the FIRST
  line of the email; placeholder triggers say so on every field using one.
- Whole centibars; local (Boise) times with am/pm; under about 40 lines.
"""

from __future__ import annotations

import html as html_lib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from bridge.analyze import FieldSnapshot
from bridge.config import Config, FieldConfig
from bridge.helios import HeliosResult
from bridge.weather import WeatherSnapshot


@dataclass
class FieldReport:
    """Everything one field contributes to the emails."""

    cfg: FieldConfig
    snapshot: FieldSnapshot
    helios: HeliosResult | None = None
    rain: WeatherSnapshot | None = None
    yesterday: tuple[float, float] | None = None  # (what we said, what happened)


@dataclass
class EmailPayload:
    subject: str
    text: str
    html: str | None = None
    attachments: list[str] = field(default_factory=list)


def _fmt_time(ts, tz: ZoneInfo) -> str:
    local = pd.Timestamp(ts).tz_convert(tz)
    return local.strftime("%-I:%M %p").lower()


def _fmt_day_time(ts, tz: ZoneInfo) -> str:
    local = pd.Timestamp(ts).tz_convert(tz)
    return local.strftime("%a %-I:%M %p").replace("AM", "am").replace("PM", "pm")


def _cb(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f} cb"


def _twelve_line(report: FieldReport) -> str:
    snap = report.snapshot
    parts = [f'  12":  {_cb(snap.d12.primary)}']
    dry = snap.dry
    if dry.status == "ok":
        rate = dry.slope_cb_per_day
        rate_text = f"{rate:.1f}" if rate < 1.5 else f"{rate:.0f}"
        parts.append(f"drying about {rate_text} cb/day")
    elif dry.status == "flat":
        parts.append("holding flat")
    elif dry.status == "insufficient":
        parts.append("not enough readings yet for a rate")
    # fresh_reset gets its own sentence below instead of a rate.
    if snap.d12.others_mean is not None:
        parts.append(f"(other probes average {snap.d12.others_mean:.0f})")
    return "   ".join(parts)


def _trigger_line(report: FieldReport, tz: ZoneInfo) -> list[str]:
    cfg, snap = report.cfg, report.snapshot
    lines: list[str] = []
    trigger = cfg.trigger_cb
    if snap.dry.status == "fresh_reset" and snap.last_wet is not None:
        lines.append(
            f"  Irrigated or wetted {_fmt_day_time(snap.last_wet.ts, tz)}; "
            "new dry-down starting, no rate yet."
        )
    outlook = snap.outlook12
    if outlook.status == "at_or_past":
        since = (
            f" since {_fmt_day_time(outlook.crossed_since, tz)}"
            if outlook.crossed_since is not None
            else ""
        )
        lines.append(f"  At or past your number ({trigger:.0f}){since}.")
    elif outlook.status == "eta" and outlook.eta_text:
        if outlook.eta_text.startswith("more than"):
            lines.append(f"  At this rate: your {trigger:.0f} is {outlook.eta_text}.")
        else:
            lines.append(f"  At this rate: reaches your {trigger:.0f} on {outlook.eta_text}.")
    if cfg.trigger_is_placeholder and lines:
        lines[-1] += "  (placeholder trigger 50 cb — reply with your number)"
    elif cfg.trigger_is_placeholder:
        lines.append("  (placeholder trigger 50 cb — reply with your number)")
    return lines


def _eighteen_lines(report: FieldReport, tz: ZoneInfo) -> list[str]:
    snap = report.snapshot
    lines = [f'  18":  {_cb(snap.d18.primary)}']
    if snap.mention_18:
        o18 = snap.outlook18
        if o18.status == "at_or_past":
            lines.append(
                f'  The 18" depth is already at or past your number '
                f"({report.cfg.trigger_cb:.0f})."
            )
        elif o18.status == "eta" and o18.eta_text:
            lines.append(f'  The 18" depth would get there sooner: {o18.eta_text}.')
    return lines


def _rain_line(report: FieldReport, now_utc: datetime, tz: ZoneInfo) -> str:
    rain = report.rain
    if rain is None:
        return "  Rain forecast unavailable this morning."
    through = pd.Timestamp(now_utc).tz_convert(tz) + timedelta(hours=72)
    day = through.strftime("%A")
    if not rain.mentionable_precip():
        return f"  Rain in the forecast: none through {day}."
    return (
        f'  Rain in the forecast: about {rain.precip_72_in:.1f}" by {day}'
        f' ({rain.precip_24_in:.1f}" in the next 24 hours).'
    )


def _helios_line(report: FieldReport, now_utc: datetime, tz: ZoneInfo) -> str:
    helios = report.helios
    if helios is None or helios.status != "ok" or helios.forecast_24 is None:
        return "  Helios pilot forecast: unavailable this morning."
    when = pd.Timestamp(now_utc).tz_convert(tz) + timedelta(hours=24)
    when_text = f"tomorrow {when.strftime('%-I:%M %p').lower()}"
    line = (
        f"  Helios pilot forecast for {when_text}: "
        f'{helios.forecast_24:.0f} cb at 12"'
    )
    if report.yesterday is not None:
        said, actual = report.yesterday
        line += f"   (yesterday it said {said:.0f}; actual was {actual:.0f})"
    return line


def _field_block(report: FieldReport, now_utc: datetime, tz: ZoneInfo) -> list[str]:
    cfg, snap = report.cfg, report.snapshot
    lines = [f"{cfg.name.upper()}  ({cfg.crop}, {cfg.acres:.0f} ac)"]
    if snap.as_of_utc is None:
        lines.append("  No readings at all — see the note below.")
        return lines
    if snap.stale:
        lines.append(
            f"  NOTE: no new readings since {_fmt_day_time(snap.as_of_utc, tz)} — "
            "the numbers below are that old."
        )
    lines.append(_twelve_line(report))
    lines.extend(_eighteen_lines(report, tz))
    lines.extend(_trigger_line(report, tz))
    lines.append(_rain_line(report, now_utc, tz))
    lines.append(_helios_line(report, now_utc, tz))
    return lines


def _notes_section(
    reports: list[FieldReport], now_utc: datetime, tz: ZoneInfo
) -> list[str]:
    notes: list[str] = []
    rain_unchecked: list[str] = []
    for report in reports:
        snap = report.snapshot
        if snap.as_of_utc is None:
            notes.append(f"{report.cfg.name}: no readings came through at all this run.")
            continue
        if snap.stale:
            notes.append(
                f"{report.cfg.name}: sensors last reported "
                f"{_fmt_day_time(snap.as_of_utc, tz)}; we flagged it and are watching."
            )
        for sensor_id, value in snap.handline_latest.items():
            depth = sensor_id.rsplit("_", 1)[1]
            notes.append(
                f'{report.cfg.name}: the handline probe ({depth}") reads '
                f"{value:.0f} cb — shown for reference, not counted in the "
                "headline numbers."
            )
        # Only flag an unverified "irrigation" call when it is recent enough
        # to matter (within the last 3 days), and say it once for all fields.
        if (
            snap.last_wet is not None
            and not snap.last_wet.rain_known
            and pd.Timestamp(now_utc) - snap.last_wet.ts <= timedelta(hours=72)
        ):
            rain_unchecked.append(report.cfg.name)
    if rain_unchecked:
        notes.append(
            f"{', '.join(rain_unchecked)}: the recent wetting looks like "
            "irrigation, but we could not check rain records this morning."
        )
    return notes


def _through_time(reports: list[FieldReport]) -> pd.Timestamp | None:
    stamps = [r.snapshot.as_of_utc for r in reports if r.snapshot.as_of_utc is not None]
    return max(stamps) if stamps else None


def compose_jacob_email(
    cfg: Config,
    reports: list[FieldReport],
    now_utc: datetime,
    mae: dict[str, float] | None = None,
) -> EmailPayload:
    """Jacob's morning email. Plain text first; simple HTML alternative."""
    tz = cfg.tz
    now_local = pd.Timestamp(now_utc).tz_convert(tz)
    through = _through_time(reports)
    through_text = _fmt_time(through, tz) if through is not None else "—"
    subject = (
        f"Helios — {now_local.strftime('%A %b %-d')} — readings through {through_text}"
    )

    lines: list[str] = []
    stale_names = [r.cfg.name for r in reports if r.snapshot.stale]
    if stale_names:
        lines.append(
            "Heads up: no fresh sensor data from "
            + ", ".join(stale_names)
            + " — details below."
        )
        lines.append("")

    for i, report in enumerate(reports):
        lines.extend(_field_block(report, now_utc, tz))
        if i < len(reports) - 1:
            lines.append("")

    notes = _notes_section(reports, now_utc, tz)
    if notes:
        lines.append("")
        lines.append("Notes:")
        lines.extend(f"  - {note}" for note in notes)

    grade_text = "we grade it against your sensors every day"
    if mae and mae.get("helios_model"):
        grade_text += (
            f"; over the last two weeks it has been off by about "
            f"{mae['helios_model']:.1f} cb on average"
        )
    contact = "Henry Turcotte"
    if cfg.henry_phone:
        contact += f", {cfg.henry_phone}"
    if cfg.henry_email:
        contact += f", {cfg.henry_email}"

    lines += [
        "",
        "--",
        "Helios is in pilot. The forecast line is our model's estimate and "
        f"{grade_text}. The model is under review and its forecast is not "
        "something to irrigate on by itself.",
        'The "at this rate" line is plain arithmetic on your own readings. '
        "Your trigger number came from you — reply to change it.",
        f"Questions any time: {contact}.",
    ]

    text = "\n".join(lines)
    body_html = html_lib.escape(text)
    html = (
        "<div style=\"font-family: -apple-system, Segoe UI, sans-serif\">"
        f"<pre style=\"font-family: inherit; white-space: pre-wrap\">{body_html}</pre>"
        "</div>"
    )
    return EmailPayload(subject=subject, text=text, html=html)


def compose_status_email(
    cfg: Config,
    status: str,  # "OK" | "WARN" | "FAIL"
    run_date_local: str,
    now_utc: datetime,
    stages: dict[str, dict],
    warnings: list[str],
    jacob_email: EmailPayload | None,
    jacob_was_sent_to: str | None,
    fields_ok: int,
    fields_total: int,
    attachments: list[str] | None = None,
) -> EmailPayload:
    """Henry's status email — always sent, even when everything else failed."""
    through = None
    for stage in stages.values():
        through = stage.get("through") or through
    through_text = through or "—"
    subject = (
        f"Bridge {status} — {run_date_local} — {fields_ok}/{fields_total} fields "
        f"— through {through_text}"
    )

    lines = [
        f"Morning run finished: {status}.",
        "",
        "STAGES",
    ]
    for name, info in stages.items():
        detail = info.get("detail", "")
        lines.append(f"  {name:<10} {info.get('status', '?'):<5} {detail}".rstrip())

    if warnings:
        lines += ["", "WARNINGS"]
        lines += [f"  - {w}" for w in warnings]

    if jacob_email is not None:
        if jacob_was_sent_to:
            heading = f"JACOB'S EMAIL (sent to {jacob_was_sent_to})"
        else:
            heading = "JACOB'S EMAIL (dry run — not sent to Jacob; shown as it would go out)"
        lines += ["", heading, "-" * 60, f"Subject: {jacob_email.subject}", ""]
        lines.append(jacob_email.text)
    else:
        lines += ["", "Jacob's email could not be composed this run — see stages above."]

    lines += [
        "",
        "--",
        "This is the bridge's own status note. If something is red, open a new "
        "Claude Code session on the irrocloud-bridge repository, paste this "
        "email, and say \"fix this\".",
    ]
    return EmailPayload(
        subject=subject,
        text="\n".join(lines),
        attachments=list(attachments or []),
    )
