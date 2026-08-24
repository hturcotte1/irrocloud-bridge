"""Email composition: honesty rules, formatting, and golden files.

The golden tests re-render the sample emails from the exact same frozen inputs
as scripts/make_samples.py and compare against the committed files in
reports/ — so the samples Henry reads can never drift from the code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import cache
from pathlib import Path

import pandas as pd

from bridge.analyze import DepthSummary, DryDown, FieldSnapshot, TriggerOutlook
from bridge.config import load_config
from bridge.message import (
    EmailPayload,
    FieldReport,
    compose_jacob_email,
    compose_status_email,
)
from bridge.weather import WeatherSnapshot
from scripts.make_samples import build_sample_emails as _build_sample_emails
from tests.test_analyze import make_field

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 8, 24, 12, 30, tzinfo=UTC)

# Rendering the samples parses every fixture; do it once for the module.
build_sample_emails = cache(_build_sample_emails)


def repo_cfg():
    return load_config(root=REPO_ROOT, environ={})


def snapshot_for(
    key: str,
    *,
    primary12=38.0,
    primary18=31.0,
    stale=False,
    as_of=None,
    dry=None,
    outlook12=None,
    outlook18=None,
    last_wet=None,
    handline=None,
) -> FieldSnapshot:
    return FieldSnapshot(
        field_key=key,
        as_of_utc=pd.Timestamp(as_of or NOW) if (as_of or not stale) else pd.Timestamp(as_of),
        stale=stale,
        d12=DepthSummary(12.0, primary=primary12, others_mean=35.0),
        d18=DepthSummary(18.0, primary=primary18),
        dry=dry or DryDown(status="ok", slope_cb_per_day=3.0),
        last_wet=last_wet,
        outlook12=outlook12 or TriggerOutlook(status="eta", days=4.0,
                                              eta_text="Thursday afternoon (Aug 27)"),
        outlook18=outlook18 or TriggerOutlook(status="none"),
        mention_18=outlook18 is not None,
        handline_latest=handline or {},
    )


def simple_report(**kwargs) -> FieldReport:
    field = kwargs.pop("field", make_field())
    return FieldReport(
        cfg=field,
        snapshot=kwargs.pop("snapshot", snapshot_for(field.key)),
        helios=kwargs.pop("helios", None),
        rain=kwargs.pop("rain", WeatherSnapshot(0.0, 0.0, 0.0)),
        yesterday=kwargs.pop("yesterday", None),
    )


# ---- golden files -----------------------------------------------------------


def test_golden_sample_emails_match_committed_files():
    jacob, status = build_sample_emails()
    morning = (REPO_ROOT / "reports" / "sample-morning-email.txt").read_text()
    assert jacob.subject in morning
    assert jacob.text in morning
    status_file = (REPO_ROOT / "reports" / "sample-status-email.txt").read_text()
    assert status.subject in status_file
    assert status.text in status_file


def test_sample_jacob_email_stays_short():
    jacob, _ = build_sample_emails()
    assert len(jacob.text.splitlines()) <= 45  # "under about 40 lines"


# ---- honesty rules ----------------------------------------------------------


def test_no_recommend_and_no_decision_words_for_jacob():
    jacob, _ = build_sample_emails()
    lowered = jacob.text.lower()
    assert "recommend" not in lowered
    # Helios's water/wait decision must never reach Jacob — it reads as an
    # instruction. (The word "watering" etc. would trip this too; good.)
    assert "decision" not in lowered
    assert "\n  water" not in lowered and "irrigate now" not in lowered


def test_pilot_and_review_framing_always_present():
    jacob, _ = build_sample_emails()
    assert "Helios pilot forecast" in jacob.text
    assert "under review" in jacob.text
    assert "not something to irrigate on by itself" in jacob.text
    assert "arithmetic on your own readings" in jacob.text


def test_stale_field_is_first_line():
    field = make_field()
    snap = snapshot_for(
        field.key, stale=True, as_of=datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
    )
    payload = compose_jacob_email(
        repo_cfg(), [simple_report(field=field, snapshot=snap)], NOW
    )
    assert payload.text.splitlines()[0].startswith("Heads up: no fresh sensor data")
    assert "no new readings since" in payload.text.lower()


def test_placeholder_trigger_flagged_on_every_field_using_it():
    reports = [simple_report(field=make_field()), simple_report(field=make_field(key="rv80"))]
    payload = compose_jacob_email(repo_cfg(), reports, NOW)
    assert payload.text.count("placeholder trigger 50 cb — reply with your number") == 2


def test_real_trigger_not_flagged():
    field = make_field()
    object.__setattr__(field, "trigger_is_placeholder", False)
    payload = compose_jacob_email(repo_cfg(), [simple_report(field=field)], NOW)
    assert "placeholder" not in payload.text


def test_at_or_past_wording():
    snap = snapshot_for(
        "cunningham-6",
        primary12=57.0,
        outlook12=TriggerOutlook(
            status="at_or_past",
            crossed_since=pd.Timestamp("2026-08-21 22:00", tz="UTC"),
        ),
    )
    payload = compose_jacob_email(repo_cfg(), [simple_report(snapshot=snap)], NOW)
    assert "At or past your number (50) since" in payload.text


def test_rain_unavailable_wording():
    payload = compose_jacob_email(repo_cfg(), [simple_report(rain=None)], NOW)
    assert "Rain forecast unavailable this morning." in payload.text


def test_helios_unavailable_wording():
    payload = compose_jacob_email(repo_cfg(), [simple_report(helios=None)], NOW)
    assert "Helios pilot forecast: unavailable this morning." in payload.text


def test_yesterday_scorecard_parens():
    payload = compose_jacob_email(
        repo_cfg(),
        [simple_report(yesterday=(41.0, 38.0))],
        NOW,
    )
    # No helios result → no forecast line → parens must not appear either.
    assert "yesterday it said" not in payload.text


def test_subject_carries_readings_through_time():
    payload = compose_jacob_email(repo_cfg(), [simple_report()], NOW)
    assert payload.subject.startswith("Helios — Monday Aug 24 — readings through ")


def test_whole_centibars_only():
    snap = snapshot_for("cunningham-6", primary12=38.4, primary18=30.6)
    payload = compose_jacob_email(repo_cfg(), [simple_report(snapshot=snap)], NOW)
    assert "38 cb" in payload.text and "31 cb" in payload.text
    assert "38.4" not in payload.text


# ---- status email -----------------------------------------------------------


def test_status_email_embeds_jacob_and_flags_dry_run():
    jacob = EmailPayload(subject="Helios — test", text="BODY LINE")
    payload = compose_status_email(
        repo_cfg(),
        status="WARN",
        run_date_local="2026-08-24",
        now_utc=NOW,
        stages={"fetch": {"status": "OK", "detail": "4/4", "through": "6:00 am"}},
        warnings=["something odd"],
        jacob_email=jacob,
        jacob_was_sent_to=None,
        fields_ok=3,
        fields_total=4,
    )
    assert payload.subject == "Bridge WARN — 2026-08-24 — 3/4 fields — through 6:00 am"
    assert "dry run — not sent to Jacob" in payload.text
    assert "BODY LINE" in payload.text
    assert "something odd" in payload.text


def test_status_email_survives_missing_jacob_email():
    payload = compose_status_email(
        repo_cfg(),
        status="FAIL",
        run_date_local="2026-08-24",
        now_utc=NOW,
        stages={"fetch": {"status": "FAIL", "detail": "login page changed"}},
        warnings=[],
        jacob_email=None,
        jacob_was_sent_to=None,
        fields_ok=0,
        fields_total=4,
    )
    assert "could not be composed" in payload.text
    assert payload.subject.startswith("Bridge FAIL")


def test_html_alternative_is_escaped():
    payload = compose_jacob_email(repo_cfg(), [simple_report()], NOW)
    assert payload.html is not None
    assert "<pre" in payload.html
    assert '12&quot;' in payload.html  # the inch mark must be escaped


def test_helios_forecast_line_with_scorecard():
    from bridge.helios import HeliosResult

    helios = HeliosResult(field_key="cunningham-6", status="ok", forecast_24=41.0)
    payload = compose_jacob_email(
        repo_cfg(), [simple_report(helios=helios, yesterday=(39.0, 38.0))], NOW
    )
    assert 'Helios pilot forecast for tomorrow 6:30 am: 41 cb at 12"' in payload.text
    assert "(yesterday it said 39; actual was 38)" in payload.text


def test_mae_footer_only_with_data():
    with_mae = compose_jacob_email(
        repo_cfg(), [simple_report()], NOW, mae={"helios_model": 4.8}
    )
    assert "off by about 4.8 cb on average" in with_mae.text
    without = compose_jacob_email(repo_cfg(), [simple_report()], NOW, mae={})
    assert "off by about" not in without.text
