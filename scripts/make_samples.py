"""Render the sample emails in reports/ from the synthetic fixtures.

Everything is offline and deterministic (frozen clock, canned weather), so
Henry can read exactly what Jacob's email and the status email will look like
without running anything. The golden tests render the same inputs and compare
against the committed files, so the samples can never silently drift from the
code.

Run:  python -m scripts.make_samples
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from bridge.analyze import analyze_field
from bridge.config import load_config
from bridge.helios import OfflineHelios, default_field_setup, run_helios_for_field
from bridge.message import (
    EmailPayload,
    FieldReport,
    compose_jacob_email,
    compose_status_email,
)
from bridge.parse import load_irrocloud_csv
from bridge.weather import WeatherSnapshot

REPO_ROOT = Path(__file__).resolve().parent.parent
FROZEN_NOW_UTC = datetime(2026, 8, 24, 12, 30, tzinfo=UTC)
RUN_DATE = "2026-08-24"

# Canned weather (no network in samples or tests): dry everywhere except
# Bennett 1 N, which demonstrates the rain line.
CANNED_RAIN = {
    "cunningham-6": WeatherSnapshot(0.0, 0.0, 0.0, 88.0, 28.0, 7.0, 0.0, 24.0),
    "rv80": WeatherSnapshot(0.0, 0.0, 0.05, 88.0, 28.0, 7.0, 0.0, 24.0),
    "bennett-1-n": WeatherSnapshot(0.1, 0.3, 0.3, 86.0, 33.0, 6.0, 0.0, 23.0),
    "whitted": WeatherSnapshot(0.0, 0.0, 0.0, 88.0, 28.0, 7.0, 0.0, 24.0),
}

# Demonstrates the "(yesterday it said X; actual was Y)" wording on one field.
CANNED_YESTERDAY = {"cunningham-6": (41.0, 38.0)}


def build_sample_emails() -> tuple[EmailPayload, EmailPayload]:
    cfg = load_config(root=REPO_ROOT, environ={})
    offline = OfflineHelios()
    reports: list[FieldReport] = []
    total_readings = 0
    for field_cfg in cfg.ordered_fields():
        parsed = load_irrocloud_csv(
            REPO_ROOT / "tests" / "fixtures" / "synthetic" / f"{field_cfg.key}.csv",
            field_cfg.key,
            tz_name=cfg.timezone_name,
        )
        total_readings += len(parsed.readings)
        snapshot = analyze_field(field_cfg, parsed.readings, FROZEN_NOW_UTC, cfg.tz)
        helios = run_helios_for_field(
            offline,
            default_field_setup(field_cfg),
            parsed.readings,
            CANNED_RAIN[field_cfg.key],
            FROZEN_NOW_UTC,
            RUN_DATE,
        )
        reports.append(
            FieldReport(
                cfg=field_cfg,
                snapshot=snapshot,
                helios=helios,
                rain=CANNED_RAIN[field_cfg.key],
                yesterday=CANNED_YESTERDAY.get(field_cfg.key),
            )
        )

    jacob = compose_jacob_email(cfg, reports, FROZEN_NOW_UTC, mae={"helios_model": 4.8})

    stages = {
        "fetch": {"status": "OK", "detail": "4/4 fields from built-in sample data",
                  "through": "6:00 am"},
        "parse": {"status": "OK", "detail": f"{total_readings} readings cleaned"},
        "store": {"status": "OK", "detail": "saved; duplicates skipped automatically"},
        "analyze": {"status": "OK", "detail": "4/4 fields analyzed"},
        "weather": {"status": "OK", "detail": "canned sample weather (offline)"},
        "helios": {"status": "OK", "detail": "offline stub — 4/4 forecasts, review gate on"},
        "message": {"status": "OK", "detail": "Jacob email composed"},
        "notify": {"status": "OK", "detail": "written to outbox/ (file mode)"},
    }
    status = compose_status_email(
        cfg,
        status="OK",
        run_date_local=RUN_DATE,
        now_utc=FROZEN_NOW_UTC,
        stages=stages,
        warnings=[
            "SEND_TO_JACOB is false — Jacob's email goes to Henry only, "
            "subject-prefixed [DRY RUN → Jacob].",
        ],
        jacob_email=jacob,
        jacob_was_sent_to=None,
        fields_ok=4,
        fields_total=4,
    )
    return jacob, status


def main() -> None:
    jacob, status = build_sample_emails()
    reports_dir = REPO_ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)
    morning = reports_dir / "sample-morning-email.txt"
    morning.write_text(
        "THIS IS A SAMPLE rendered from built-in synthetic data (offline mode),\n"
        "so Henry can review the wording. Numbers are not real field readings.\n"
        + "=" * 68
        + f"\nSubject: {jacob.subject}\n\n{jacob.text}\n",
        encoding="utf-8",
    )
    status_path = reports_dir / "sample-status-email.txt"
    status_path.write_text(
        "THIS IS A SAMPLE rendered from built-in synthetic data (offline mode).\n"
        + "=" * 68
        + f"\nSubject: {status.subject}\n\n{status.text}\n",
        encoding="utf-8",
    )
    print(f"wrote {morning}")
    print(f"wrote {status_path}")
    print(f"Jacob email is {len(jacob.text.splitlines())} lines")


if __name__ == "__main__":
    main()
