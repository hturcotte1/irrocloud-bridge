"""Parsing: both layouts, every cleaning rule, and stop-on-unknown-layout."""

from __future__ import annotations

import pandas as pd
import pytest

from bridge.errors import AlertError
from bridge.parse import load_irrocloud_csv, sensor_depth_in, sensor_probe


def test_modern_layout_parses(synthetic_dir):
    result = load_irrocloud_csv(synthetic_dir / "cunningham-6.csv", "cunningham-6")
    assert result.layout == "modern"
    assert result.kept_rows == 961
    ids = set(result.readings["sensor_id"])
    assert ids == {
        f"cunningham-6_{s}" for s in ["a_12", "a_18", "b_12", "b_18", "c_12", "c_18"]
    }
    assert result.readings["ts_utc"].dt.tz is not None


def test_sensor_id_convention_matches_helios():
    # Depth is the last underscore token; probe the one before (HELIOS rule).
    assert sensor_depth_in("cunningham-6_a_12") == 12.0
    assert sensor_probe("cunningham-6_a_12") == "a"
    assert sensor_depth_in("bennett-1-n_c_18") == 18.0
    assert sensor_probe("bennett-1-n_c_18") == "c"


def test_naive_timestamps_are_boise_local(synthetic_dir):
    result = load_irrocloud_csv(synthetic_dir / "cunningham-6.csv", "cunningham-6")
    first = result.readings["ts_utc"].min()
    # File starts 2026-07-15 06:00 local; Boise in July is MDT (UTC-6).
    assert first == pd.Timestamp("2026-07-15 12:00:00", tz="UTC")


def test_extra_columns_and_cleaning_flags(synthetic_dir):
    result = load_irrocloud_csv(synthetic_dir / "rv80.csv", "rv80")
    assert result.layout == "modern"  # Battery column present but ignored
    assert result.dropped_sentinel_254 >= 25  # ~30 sprinkled 254s
    assert result.dropped_negative == 3
    assert result.dropped_duplicate_ts == 5  # duplicated tail keeps last
    assert (result.readings["tension_cb"] != 254).all()
    assert (result.readings["tension_cb"] >= 0).all()


def test_duplicate_timestamps_keep_last():
    result = None
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "dup.csv"
        p.write_text(
            "Timestamp,SM1,SM2,SM3,SM4,SM5,SM6\n"
            "2026-07-01 10:00:00,30,20,31,21,32,22\n"
            "2026-07-01 10:00:00,99,20,31,21,32,22\n"
        )
        result = load_irrocloud_csv(p, "x")
    a12 = result.readings[result.readings["sensor_id"] == "x_a_12"]
    assert list(a12["tension_cb"]) == [99.0]
    assert result.dropped_duplicate_ts == 1


def test_all_null_rows_dropped(tmp_path):
    p = tmp_path / "nulls.csv"
    p.write_text(
        "Timestamp,SM1,SM2,SM3,SM4,SM5,SM6\n"
        "2026-07-01 10:00:00,30,20,31,21,32,22\n"
        "2026-07-01 11:00:00,,,,,,\n"
        "2026-07-01 12:00:00,254,-5,,,,\n"  # cleans to all-null → dropped too
        "2026-07-01 13:00:00,31,20,31,21,32,22\n"
    )
    result = load_irrocloud_csv(p, "x")
    assert result.kept_rows == 2
    assert result.dropped_all_null == 2
    assert result.dropped_sentinel_254 == 1
    assert result.dropped_negative == 1


def test_values_above_240_clip_and_flag(synthetic_dir):
    result = load_irrocloud_csv(synthetic_dir / "whitted.csv", "whitted")
    assert result.clipped_over_240 > 0
    assert result.readings["tension_cb"].max() == 240.0
    clipped = result.readings[result.readings["quality_flag"] == "clipped"]
    assert (clipped["tension_cb"] == 240.0).all()


def test_legacy_layout_parses(synthetic_dir):
    result = load_irrocloud_csv(synthetic_dir / "bennett-1-n.csv", "bennett-1-n")
    assert result.layout == "legacy"
    assert result.kept_rows == 865
    assert result.dropped_sentinel_254 > 0
    # Column 1 (battery, 3.8) must NOT have leaked in as a sensor value.
    assert not ((result.readings["tension_cb"] > 3.7) & (result.readings["tension_cb"] < 3.9)).any()


def test_unknown_layout_stops_with_first_lines(synthetic_dir):
    with pytest.raises(AlertError) as excinfo:
        load_irrocloud_csv(synthetic_dir / "bad_layout.csv", "x")
    err = excinfo.value
    assert "neither known" in err.reason
    assert any("Export Report" in line for line in err.evidence_lines)
    assert len(err.evidence_lines) <= 5


def test_empty_file_stops(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("")
    with pytest.raises(AlertError):
        load_irrocloud_csv(p, "x")


def test_modern_missing_sensor_column_stops(tmp_path):
    p = tmp_path / "missing.csv"
    p.write_text("Timestamp,SM1,SM2,SM3\n2026-07-01 10:00:00,30,20,31\n")
    with pytest.raises(AlertError) as excinfo:
        load_irrocloud_csv(p, "x")
    assert "SM4" in excinfo.value.reason


def test_zero_usable_readings_stops(tmp_path):
    p = tmp_path / "all254.csv"
    p.write_text(
        "Timestamp,SM1,SM2,SM3,SM4,SM5,SM6\n2026-07-01 10:00:00,254,254,254,254,254,254\n"
    )
    with pytest.raises(AlertError) as excinfo:
        load_irrocloud_csv(p, "x")
    assert "zero usable" in excinfo.value.reason


def test_utf8_sig_bom_tolerated(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_bytes(
        b"\xef\xbb\xbfTimestamp,SM1,SM2,SM3,SM4,SM5,SM6\n2026-07-01 10:00:00,30,20,31,21,32,22\n"
    )
    result = load_irrocloud_csv(p, "x")
    assert result.layout == "modern"
    assert result.kept_rows == 1
