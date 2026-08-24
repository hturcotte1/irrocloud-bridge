"""Storage: idempotent inserts, sent log, forecast upsert + scoring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from bridge.parse import load_irrocloud_csv
from bridge.store import Store


def make_store(tmp_path) -> Store:
    return Store(tmp_path / "bridge.sqlite")


def test_insert_readings_is_idempotent(tmp_path, synthetic_dir):
    parsed = load_irrocloud_csv(synthetic_dir / "cunningham-6.csv", "cunningham-6")
    with make_store(tmp_path) as store:
        first = store.insert_readings("cunningham-6", parsed, "fixture", "a.csv")
        second = store.insert_readings("cunningham-6", parsed, "fixture", "a.csv")
        assert first == len(parsed.readings)
        assert second == 0
        assert store.reading_counts() == {"cunningham-6": len(parsed.readings)}


def test_readings_frame_window(tmp_path, synthetic_dir, frozen_now):
    parsed = load_irrocloud_csv(synthetic_dir / "cunningham-6.csv", "cunningham-6")
    with make_store(tmp_path) as store:
        store.insert_readings("cunningham-6", parsed, "fixture", "a.csv")
        since = frozen_now - timedelta(hours=72)
        frame = store.readings_frame("cunningham-6", since_utc=since)
        assert not frame.empty
        assert frame["ts_utc"].min() >= pd.Timestamp(since)
        assert store.newest_reading_ts("cunningham-6") is not None


def test_write_readings_csv(tmp_path, synthetic_dir):
    parsed = load_irrocloud_csv(synthetic_dir / "rv80.csv", "rv80")
    with make_store(tmp_path) as store:
        store.insert_readings("rv80", parsed, "fixture", "b.csv")
        out = tmp_path / "readings" / "rv80.csv"
        n = store.write_readings_csv("rv80", out, "America/Boise")
        assert n == len(parsed.readings)
        head = out.read_text().splitlines()[0]
        assert head.startswith("ts_utc,ts_local,sensor_id")


def test_sent_log_guards_resend(tmp_path):
    with make_store(tmp_path) as store:
        assert not store.already_sent("2026-08-24", "jacob")
        store.record_sent("2026-08-24", "jacob", "Helios — Mon Aug 24")
        assert store.already_sent("2026-08-24", "jacob")
        # Henry's status email is never guarded — it must always go out.
        assert not store.already_sent("2026-08-24", "henry")


def test_forecast_upsert_same_day(tmp_path):
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    target = now + timedelta(hours=24)
    with make_store(tmp_path) as store:
        store.record_forecast("rv80", now, target, 24, "helios_model", 41.0)
        store.record_forecast("rv80", now + timedelta(hours=2), target, 24, "helios_model", 43.0)
        rows = store.conn.execute("SELECT * FROM forecasts").fetchall()
        assert len(rows) == 1
        assert rows[0]["value_cb"] == 43.0  # later run of the same day wins


def test_score_forecasts_within_tolerance(tmp_path):
    made = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
    target = made + timedelta(hours=24)
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    series = pd.Series(
        [38.0, 39.0],
        index=pd.DatetimeIndex(
            [target - timedelta(minutes=30), target + timedelta(minutes=45)], tz="UTC"
        ),
    )
    with make_store(tmp_path) as store:
        store.record_forecast("rv80", made, target, 24, "helios_model", 41.0)
        scored = store.score_forecasts("rv80", series, now)
        assert scored == 1
        row = store.conn.execute("SELECT * FROM forecasts").fetchone()
        assert row["actual_cb"] == 38.0  # nearest reading wins


def test_score_forecasts_outside_tolerance_skipped(tmp_path):
    made = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
    target = made + timedelta(hours=24)
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    series = pd.Series(
        [38.0], index=pd.DatetimeIndex([target + timedelta(hours=2)], tz="UTC")
    )
    with make_store(tmp_path) as store:
        store.record_forecast("rv80", made, target, 24, "helios_model", 41.0)
        assert store.score_forecasts("rv80", series, now) == 0


def test_forecast_mae_needs_three_points(tmp_path):
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    with make_store(tmp_path) as store:
        for i in range(3):
            made = now - timedelta(days=i + 2)
            target = made + timedelta(hours=24)
            store.record_forecast("rv80", made, target, 24, "helios_model", 40.0 + i)
        # Nothing scored yet → no MAE at all.
        assert store.forecast_mae(now) == {}
        series = pd.Series(
            [38.0, 38.0, 38.0],
            index=pd.DatetimeIndex(
                [now - timedelta(days=i + 1) for i in range(3)], tz="UTC"
            ),
        )
        store.score_forecasts("rv80", series, now)
        mae = store.forecast_mae(now)
        assert "helios_model" in mae
        assert mae["helios_model"] > 0


def test_run_lifecycle(tmp_path):
    with make_store(tmp_path) as store:
        run_id = store.start_run("2026-08-24")
        store.finish_run(run_id, "OK", {"fetch": "ok"}, "henry@x.com", ["bridge-rv80-2026-08-24"])
        row = store.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        assert row["status"] == "OK"
        assert "fetch" in row["stage_status_json"]
