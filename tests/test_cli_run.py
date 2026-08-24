"""End-to-end offline run: fixtures → SQLite → analysis → stub Helios → outbox.

This is the "prove the whole pipeline" test (build brief §17): everything the
morning run does, in a temp directory, with no network anywhere.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bridge.cli import cmd_run
from bridge.config import load_config
from bridge.fetchers.fixture import FixtureFetcher
from bridge.store import Store
from bridge.weather import WeatherSnapshot
from tests.test_analyze import make_field

REPO_ROOT = Path(__file__).resolve().parent.parent
FROZEN_NOW = datetime(2026, 8, 24, 12, 30, tzinfo=UTC)


def canned_weather(lat, lon, now_utc):
    return WeatherSnapshot(0.0, 0.0, 0.0, 88.0, 30.0, 7.0, 0.0, 24.0)


def make_root(tmp_path) -> Path:
    root = tmp_path / "repo"
    (root / "tests" / "fixtures").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "fields.json", root / "fields.json")
    shutil.copytree(
        REPO_ROOT / "tests" / "fixtures" / "synthetic",
        root / "tests" / "fixtures" / "synthetic",
    )
    return root


def run_once(root, **kwargs) -> int:
    cfg = load_config(root=root, environ={})
    kwargs.setdefault("now_utc", FROZEN_NOW)
    kwargs.setdefault("weather_fn", canned_weather)
    return cmd_run(cfg, **kwargs)


def test_offline_run_end_to_end(tmp_path, capsys):
    root = make_root(tmp_path)
    rc = run_once(root)
    assert rc == 0

    # Raw exports saved per field, never parsed-and-discarded.
    raw_files = list((root / "data" / "raw").rglob("*.csv"))
    assert len(raw_files) == 4

    # Tidy human-readable history per field.
    for key in ("cunningham-6", "rv80", "bennett-1-n", "whitted"):
        assert (root / "data" / "readings" / f"{key}.csv").exists()

    # The database holds readings and today's forecasts.
    store = Store(root / "data" / "bridge.sqlite")
    counts = store.reading_counts()
    assert set(counts) == {"cunningham-6", "rv80", "bennett-1-n", "whitted"}
    forecasts = store.conn.execute(
        "SELECT DISTINCT source FROM forecasts"
    ).fetchall()
    assert {r["source"] for r in forecasts} >= {"helios_model", "persistence"}
    store.close()

    # Both emails landed in the outbox: Jacob's (dry run) and Henry's status.
    outbox = sorted(p.name for p in (root / "outbox").glob("*.txt"))
    assert len(outbox) == 2
    jacob_file = next(
        (root / "outbox" / n) for n in outbox if "jacob" in n
    ).read_text()
    assert "[DRY RUN → Jacob]" in jacob_file
    assert "Helios pilot forecast" in jacob_file
    status_file = next(
        (root / "outbox" / n) for n in outbox if "status" in n
    ).read_text()
    assert "Subject: Bridge OK — 2026-08-24 — 4/4 fields" in status_file

    # last-run.json records every stage.
    log = json.loads((root / "logs" / "last-run.json").read_text())
    assert log["status"] == "OK"
    assert set(log["stages"]) >= {"fetch", "analyze", "weather", "helios", "message",
                                  "notify", "status-email"}
    assert log["stages"]["helios"]["detail"].endswith("(offline stub — review gate on)")


def test_second_run_same_day_does_not_resend_jacob(tmp_path):
    root = make_root(tmp_path)
    assert run_once(root) == 0
    assert run_once(root) == 0
    log = json.loads((root / "logs" / "last-run.json").read_text())
    assert log["stages"]["notify"]["status"] == "SKIP"
    assert "already sent today" in log["stages"]["notify"]["detail"]
    # Jacob email count unchanged (1), status emails: 2.
    outbox = [p.name for p in (root / "outbox").glob("*.txt")]
    assert sum("jacob" in n for n in outbox) == 1


def test_force_resends_jacob(tmp_path):
    root = make_root(tmp_path)
    assert run_once(root) == 0
    assert run_once(root, force=True) == 0
    outbox = [p.name for p in (root / "outbox").glob("*.txt")]
    assert sum("jacob" in n for n in outbox) == 2


def test_no_email_skips_all_sending(tmp_path):
    root = make_root(tmp_path)
    assert run_once(root, no_email=True) == 0
    assert list((root / "outbox").glob("*.txt")) == []
    log = json.loads((root / "logs" / "last-run.json").read_text())
    assert log["stages"]["notify"]["status"] == "SKIP"
    assert log["stages"]["status-email"]["status"] == "SKIP"


def test_weather_failure_never_fails_the_run(tmp_path):
    root = make_root(tmp_path)
    rc = run_once(root, weather_fn=lambda lat, lon, now: None)
    assert rc == 0
    log = json.loads((root / "logs" / "last-run.json").read_text())
    assert log["stages"]["weather"]["status"] == "WARN"
    jacob_file = next(
        p for p in (root / "outbox").glob("*.txt") if "jacob" in p.name
    ).read_text()
    assert "Rain forecast unavailable this morning." in jacob_file


def test_run_is_idempotent_in_the_store(tmp_path):
    root = make_root(tmp_path)
    run_once(root)
    store = Store(root / "data" / "bridge.sqlite")
    first_counts = store.reading_counts()
    store.close()
    run_once(root, force=True)
    store = Store(root / "data" / "bridge.sqlite")
    assert store.reading_counts() == first_counts  # overlap deduped
    forecast_rows = store.conn.execute("SELECT COUNT(*) AS n FROM forecasts").fetchone()
    store.close()
    # Re-run upserted, not duplicated: ≤ 3 sources × 4 fields.
    assert forecast_rows["n"] <= 12


# ---- fixture fetcher ---------------------------------------------------------


def make_cfg(tmp_path):
    root = make_root(tmp_path)
    return load_config(root=root, environ={})


def test_fixture_fetcher_no_shift_when_fresh(tmp_path):
    cfg = make_cfg(tmp_path)
    fetcher = FixtureFetcher(cfg, now_utc=FROZEN_NOW)
    path = fetcher.fetch(
        make_field(), (FROZEN_NOW - timedelta(days=7)).date(), FROZEN_NOW.date()
    )
    lines = path.read_text().splitlines()
    assert lines[0].startswith("Timestamp,")
    assert lines[-1].startswith("2026-08-24 06:00:00")  # untouched


def test_fixture_fetcher_shifts_stale_data_fresh(tmp_path):
    cfg = make_cfg(tmp_path)
    later = datetime(2026, 10, 15, 13, 30, tzinfo=UTC)  # ~7:30 am Boise (MDT)
    fetcher = FixtureFetcher(cfg, now_utc=later)
    path = fetcher.fetch(
        make_field(), (later - timedelta(days=7)).date(), later.date()
    )
    newest = path.read_text().splitlines()[-1].split(",")[0]
    assert newest == "2026-10-15 05:00:00"  # now (7:30) − 2 h, floored to the hour


def test_fixture_fetcher_preserves_legacy_junk_lines(tmp_path):
    cfg = make_cfg(tmp_path)
    fetcher = FixtureFetcher(cfg, now_utc=FROZEN_NOW)
    path = fetcher.fetch(
        make_field(key="bennett-1-n"),
        (FROZEN_NOW - timedelta(days=7)).date(),
        FROZEN_NOW.date(),
    )
    lines = path.read_text().splitlines()
    assert lines[0].startswith("IRROMETER")
    assert lines[1].startswith("Device:")


def test_fixture_fetcher_window_filter(tmp_path):
    cfg = make_cfg(tmp_path)
    fetcher = FixtureFetcher(cfg, now_utc=FROZEN_NOW)
    start = (FROZEN_NOW - timedelta(days=3)).date()
    path = fetcher.fetch(make_field(), start, FROZEN_NOW.date())
    stamps = [line.split(",")[0] for line in path.read_text().splitlines()[1:]]
    assert min(stamps)[:10] == start.isoformat()
