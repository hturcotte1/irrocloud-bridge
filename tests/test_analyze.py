"""Analysis rules: primary selection, wetting/artifacts, slope, trigger."""

from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from bridge.analyze import (
    DryDown,
    analyze_field,
    detect_wetting_events,
    dry_down_rate,
    last_wetting,
    pivot_depth,
    primary_frame,
    trigger_outlook,
)
from bridge.config import FieldConfig
from bridge.parse import load_irrocloud_csv

TZ = ZoneInfo("America/Boise")


def make_field(key="cunningham-6", probes=None, trigger=50.0) -> FieldConfig:
    return FieldConfig(
        key=key,
        name=key,
        crop="corn",
        acres=100.0,
        acres_note=None,
        lat=43.0,
        lon=-116.1,
        planted="2026-05-02",
        irrocloud_device_name=None,
        probes=probes or {"a": "pivot", "b": "pivot", "c": "pivot"},
        probes_note=None,
        trigger_cb=trigger,
        trigger_is_placeholder=True,
    )


def hourly_index(n: int, start="2026-08-20 00:00"):
    return pd.date_range(start, periods=n, freq="1h", tz="UTC")


def wide_from(rows: dict[str, list[float]], index) -> pd.DataFrame:
    return pd.DataFrame(rows, index=index)


# ---- primary selection ----------------------------------------------------


def test_mad_outlier_dropped_and_driest_inlier_wins():
    idx = hourly_index(1)
    wide = wide_from({"x_a_12": [40.0], "x_b_12": [41.0], "x_c_12": [235.0]}, idx)
    frame = primary_frame(wide)
    assert frame.iloc[0]["primary"] == 41.0  # 235 is a MAD outlier; driest inlier
    assert frame.iloc[0]["primary_sensor"] == "x_b_12"
    assert frame.iloc[0]["others_mean"] == 40.0


def test_fewer_than_three_sensors_skips_mad_filter():
    idx = hourly_index(1)
    wide = wide_from({"x_a_12": [40.0], "x_c_12": [235.0]}, idx)
    frame = primary_frame(wide)
    assert frame.iloc[0]["primary"] == 235.0  # no filter with only two sensors


def test_primary_tie_broken_by_sensor_id():
    idx = hourly_index(1)
    wide = wide_from({"x_c_12": [41.0], "x_a_12": [41.0], "x_b_12": [40.0]}, idx)
    frame = primary_frame(wide)
    assert frame.iloc[0]["primary_sensor"] == "x_a_12"


# ---- wetting events ---------------------------------------------------------


def _primary_and_wide(values: list[float], start="2026-08-20 00:00"):
    idx = hourly_index(len(values), start)
    wide = wide_from({"x_a_12": values}, idx)
    return pd.Series(values, index=idx, dtype=float), wide


def test_simple_drop_is_irrigation_when_no_rain():
    series, wide = _primary_and_wide([50, 52, 54, 30, 31])
    events = detect_wetting_events(series, wide, precip_by_day={}, tz=TZ)
    assert len(events) == 1
    assert events[0].label == "irrigation"
    assert events[0].drop_cb == 24.0
    assert events[0].rain_known


def test_drop_with_rain_that_day_is_ambiguous():
    series, wide = _primary_and_wide([50, 52, 54, 30, 31])
    rain = {events_day: 0.3 for events_day in [date(2026, 8, 19), date(2026, 8, 20)]}
    events = detect_wetting_events(series, wide, precip_by_day=rain, tz=TZ)
    assert events[0].label == "ambiguous"


def test_drop_with_unknown_rain_flags_low_confidence():
    series, wide = _primary_and_wide([50, 52, 54, 30, 31])
    events = detect_wetting_events(series, wide, precip_by_day=None, tz=TZ)
    assert events[0].label == "irrigation"
    assert not events[0].rain_known
    assert "rain unknown" in events[0].label_text()


def test_ceiling_artifact():
    series, wide = _primary_and_wide([220, 226, 205, 206])
    events = detect_wetting_events(series, wide, precip_by_day={}, tz=TZ)
    assert [e.label for e in events] == ["artifact"]  # prev ≥ 225


def test_huge_drop_artifact():
    series, wide = _primary_and_wide([100, 200, 80, 81])
    events = detect_wetting_events(series, wide, precip_by_day={}, tz=TZ)
    assert [e.label for e in events] == ["artifact"]  # drop > 100


def test_every_probe_dropping_at_once_is_artifact():
    idx = hourly_index(3)
    wide = wide_from(
        {"x_a_12": [50, 51, 28], "x_b_12": [52, 52, 30], "x_c_12": [51, 51, 29]}, idx
    )
    primary = primary_frame(wide)["primary"]
    events = detect_wetting_events(primary, wide, precip_by_day={}, tz=TZ)
    assert [e.label for e in events] == ["artifact"]


def test_gap_over_three_hours_breaks_continuity():
    idx = pd.DatetimeIndex(
        ["2026-08-20 00:00", "2026-08-20 01:00", "2026-08-20 05:30"], tz="UTC"
    )
    series = pd.Series([50.0, 51.0, 20.0], index=idx)
    wide = wide_from({"x_a_12": [50.0, 51.0, 20.0]}, idx)
    events = detect_wetting_events(series, wide, precip_by_day={}, tz=TZ)
    assert events == []  # the drop sits across a 4.5 h gap


def test_last_wetting_skips_artifacts():
    series, wide = _primary_and_wide([100, 200, 80, 82, 84, 60, 61])
    events = detect_wetting_events(series, wide, precip_by_day={}, tz=TZ)
    assert [e.label for e in events] == ["artifact", "irrigation"]
    assert last_wetting(events).label == "irrigation"


# ---- dry-down slope ---------------------------------------------------------


def test_slope_recovers_linear_rate(frozen_now):
    idx = pd.date_range(end=frozen_now, periods=49, freq="1h", tz="UTC")
    series = pd.Series([20 + 3.0 * i / 24 for i in range(49)], index=idx)
    dry = dry_down_rate(series, None, frozen_now)
    assert dry.status == "ok"
    assert dry.slope_cb_per_day == pytest.approx(3.0, abs=0.05)


def test_flat_series_reports_flat(frozen_now):
    idx = pd.date_range(end=frozen_now, periods=49, freq="1h", tz="UTC")
    dry = dry_down_rate(pd.Series([30.0] * 49, index=idx), None, frozen_now)
    assert dry.status == "flat"


def test_fresh_reset_beats_everything(frozen_now):
    idx = pd.date_range(end=frozen_now, periods=49, freq="1h", tz="UTC")
    series = pd.Series([20 + 3.0 * i / 24 for i in range(49)], index=idx)
    dry = dry_down_rate(series, pd.Timestamp(frozen_now) - timedelta(hours=10), frozen_now)
    assert dry.status == "fresh_reset"
    assert dry.slope_cb_per_day is None


def test_too_few_readings_is_insufficient(frozen_now):
    idx = pd.date_range(end=frozen_now, periods=4, freq="1h", tz="UTC")
    dry = dry_down_rate(pd.Series([20.0, 21, 22, 23], index=idx), None, frozen_now)
    assert dry.status == "insufficient"


def test_window_capped_to_72h_and_starts_at_wetting(frozen_now):
    # 10 days of history, wetting 48 h ago; only the post-wetting points count.
    idx = pd.date_range(end=frozen_now, periods=241, freq="1h", tz="UTC")
    values = [200 - i for i in range(241)]  # nonsense before the wetting
    series = pd.Series(values, index=idx, dtype=float)
    wet_at = pd.Timestamp(frozen_now) - timedelta(hours=48)
    post = series.index >= wet_at
    series[post] = [10 + 4.0 * i / 24 for i in range(post.sum())]
    dry = dry_down_rate(series, wet_at, frozen_now)
    assert dry.status == "ok"
    assert dry.slope_cb_per_day == pytest.approx(4.0, abs=0.1)
    assert dry.window_start >= wet_at


# ---- trigger outlook --------------------------------------------------------


def test_eta_arithmetic(frozen_now):
    idx = pd.date_range(end=frozen_now, periods=49, freq="1h", tz="UTC")
    series = pd.Series([30 + 3.0 * i / 24 for i in range(49)], index=idx)
    dry = DryDown(status="ok", slope_cb_per_day=3.0)
    outlook = trigger_outlook(series, 38.0, dry, 50.0, frozen_now, TZ)
    assert outlook.status == "eta"
    assert outlook.days == pytest.approx(4.0)
    assert outlook.eta_text is not None and "(" in outlook.eta_text  # weekday (date)


def test_eta_beyond_a_week_is_softened(frozen_now):
    dry = DryDown(status="ok", slope_cb_per_day=1.0)
    series = pd.Series(dtype=float)
    outlook = trigger_outlook(series, 40.0, dry, 50.0, frozen_now, TZ)
    assert outlook.status == "eta"
    assert outlook.eta_text == "more than a week out at this rate"


def test_at_or_past_reports_since_when(frozen_now):
    idx = pd.date_range(end=frozen_now, periods=6, freq="1h", tz="UTC")
    series = pd.Series([48.0, 49.0, 51.0, 52.0, 53.0, 54.0], index=idx)
    outlook = trigger_outlook(series, 54.0, DryDown(status="ok", slope_cb_per_day=2.0),
                              50.0, frozen_now, TZ)
    assert outlook.status == "at_or_past"
    assert outlook.crossed_since == idx[2]  # first reading of the ≥50 run


def test_no_eta_when_flat(frozen_now):
    outlook = trigger_outlook(
        pd.Series(dtype=float), 38.0, DryDown(status="flat", slope_cb_per_day=0.2),
        50.0, frozen_now, TZ,
    )
    assert outlook.status == "none"


# ---- whole-field snapshots on the synthetic season -------------------------


def _readings(synthetic_dir, key):
    return load_irrocloud_csv(synthetic_dir / f"{key}.csv", key).readings


def test_cunningham_snapshot(synthetic_dir, frozen_now):
    snap = analyze_field(make_field(), _readings(synthetic_dir, "cunningham-6"),
                         frozen_now, TZ)
    assert not snap.stale
    assert 30 <= snap.d12.primary <= 45
    assert snap.dry.status == "ok"
    assert 2.0 <= snap.dry.slope_cb_per_day <= 4.5
    assert snap.outlook12.status == "eta"
    assert snap.last_wet is not None and snap.last_wet.label != "artifact"


def test_whitted_past_trigger_and_artifact_ignored(synthetic_dir, frozen_now):
    snap = analyze_field(make_field(key="whitted"), _readings(synthetic_dir, "whitted"),
                         frozen_now, TZ)
    assert snap.outlook12.status == "at_or_past"
    # The ceiling glitch (≈ Jul 30) must NOT count as the last wetting; the
    # real irrigation ≈ Aug 10 must.
    assert snap.last_wet.ts > pd.Timestamp("2026-08-05", tz="UTC")


def test_bennett_fresh_reset(synthetic_dir, frozen_now):
    snap = analyze_field(make_field(key="bennett-1-n"),
                         _readings(synthetic_dir, "bennett-1-n"), frozen_now, TZ)
    assert snap.dry.status == "fresh_reset"
    assert snap.outlook12.status == "none"


def test_rv80_stuck_probe_excluded_by_mad(synthetic_dir, frozen_now):
    snap = analyze_field(make_field(key="rv80"), _readings(synthetic_dir, "rv80"),
                         frozen_now, TZ)
    # During the stuck window (probe c pinned at ~185) the primary must follow
    # the healthy probes, not the stuck one — at a timestamp where the healthy
    # probes differ. (When they are identical, MAD is 0 and the HELIOS rule
    # deliberately skips the filter, so the stuck probe would lead; the port
    # keeps that behavior.)
    stuck_ts = pd.Timestamp("2026-08-04 12:00", tz="UTC")  # 06:00 Boise
    assert snap.primary12_series[stuck_ts] < 100


def test_handline_probe_excluded_but_shown(synthetic_dir, frozen_now):
    cfg = make_field(key="whitted", probes={"a": "pivot", "b": "pivot", "c": "handline"})
    snap = analyze_field(cfg, _readings(synthetic_dir, "whitted"), frozen_now, TZ)
    assert "whitted_c_12" in snap.handline_latest
    assert snap.d12.primary_sensor in ("whitted_a_12", "whitted_b_12")


def test_stale_field_detected(synthetic_dir):
    late = pd.Timestamp("2026-08-26 12:00", tz="UTC")  # 48 h after data ends
    snap = analyze_field(make_field(), _readings(synthetic_dir, "cunningham-6"), late, TZ)
    assert snap.stale


def test_empty_readings_never_crash(frozen_now):
    snap = analyze_field(make_field(), pd.DataFrame(
        columns=["sensor_id", "probe", "depth_in", "ts_utc", "ts_raw", "tension_cb",
                 "quality_flag"]), frozen_now, TZ)
    assert snap.stale
    assert snap.d12.primary is None
    assert snap.notes


def test_pivot_depth_excludes_sensors(synthetic_dir):
    readings = _readings(synthetic_dir, "whitted")
    wide = pivot_depth(readings, 12.0, exclude_sensor_ids={"whitted_c_12"})
    assert "whitted_c_12" not in wide.columns
    assert {"whitted_a_12", "whitted_b_12"} <= set(wide.columns)
