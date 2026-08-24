"""Raw IrroCloud export CSV → tidy readings.

This is a port of HELIOS's ``helios/scripts/parse_irrocloud_data.py``
(``load_irrocloud_csv``), reconstructed from Appendix A of the build brief
because the snapshot download was blocked tonight (see
bridge/_helios_schemas/PROVENANCE.md). The cleaning rules are kept identical so
the bridge and Helios agree about what a reading means:

- two layouts (modern header / legacy skip-2-junk-lines);
- ``254`` is a sentinel for a bad or disconnected reading → dropped;
- negative values → dropped; values above 240 → clipped to 240 and flagged;
- rows with no timestamp or no sensor values → dropped;
- sorted by time, duplicate timestamps keep the last.

One deliberate difference: HELIOS parses timestamps with ``utc=True``; real
IrroCloud exports may be local time with no offset, so the bridge treats naive
timestamps as ``America/Boise`` (configurable) and stores both the raw text and
the UTC instant. The morning discovery run confirms the site's real timezone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from bridge.errors import AlertError

MAX_TENSION_CB = 240.0
SENTINEL_BAD_READING = 254.0

# SM1..SM6 map, in order, to probe a/b/c at 12" and 18" (Appendix A).
SM_COLUMNS = ["SM1", "SM2", "SM3", "SM4", "SM5", "SM6"]
SENSOR_SUFFIXES = ["a_12", "a_18", "b_12", "b_18", "c_12", "c_18"]

READING_COLUMNS = [
    "sensor_id",
    "probe",
    "depth_in",
    "ts_utc",
    "ts_raw",
    "tension_cb",
    "quality_flag",
]


def sensor_depth_in(sensor_id: str) -> float:
    """Depth is the last underscore-separated token — the HELIOS convention."""
    return float(sensor_id.rsplit("_", 1)[1])


def sensor_probe(sensor_id: str) -> str:
    """Probe letter is the second-to-last underscore-separated token."""
    return sensor_id.rsplit("_", 2)[-2]


@dataclass
class ParseResult:
    """Tidy readings plus an accounting of everything that was dropped or
    changed, so the run log can say exactly what the cleaning did."""

    readings: pd.DataFrame  # columns: READING_COLUMNS
    layout: str  # "modern" | "legacy"
    raw_rows: int
    kept_rows: int
    dropped_sentinel_254: int = 0
    dropped_negative: int = 0
    clipped_over_240: int = 0
    dropped_no_timestamp: int = 0
    dropped_all_null: int = 0
    dropped_duplicate_ts: int = 0

    def summary(self) -> str:
        return (
            f"{self.layout} layout: {self.raw_rows} rows in file, "
            f"{self.kept_rows} timestamps kept, {len(self.readings)} sensor readings; "
            f"dropped {self.dropped_sentinel_254} bad-sensor 254s, "
            f"{self.dropped_negative} negatives, {self.dropped_no_timestamp} bad timestamps, "
            f"{self.dropped_all_null} empty rows, {self.dropped_duplicate_ts} duplicate "
            f"timestamps; clipped {self.clipped_over_240} readings above 240."
        )


def _first_lines(path: Path, n: int = 5) -> list[str]:
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            return [next(fh).rstrip("\n") for _ in range(n)]
    except StopIteration:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            return [line.rstrip("\n") for line in fh]
    except OSError:
        return []


def _read_raw_frame(path: Path) -> tuple[pd.DataFrame, str]:
    """Detect the layout and return (frame with ts_raw + SM1..SM6, layout name).

    Raises AlertError, with the first five lines as evidence, when the file
    matches neither known layout — the brief's "third layout means stop".
    """
    lines = _first_lines(path, 5)
    if not lines or all(not line.strip() for line in lines):
        raise AlertError(
            f"The export file {path.name} is empty. IrroCloud returned no data.",
            evidence_lines=lines,
        )

    if lines[0].startswith("Timestamp,"):
        frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
        missing = [c for c in ["Timestamp", *SM_COLUMNS] if c not in frame.columns]
        if missing:
            raise AlertError(
                f"The export file {path.name} looks like the modern layout but is "
                f"missing the column(s) {', '.join(missing)}. The site may have "
                "changed its export format.",
                evidence_lines=lines,
            )
        out = frame[["Timestamp", *SM_COLUMNS]].copy()
        out.columns = ["ts_raw", *SM_COLUMNS]
        return out, "modern"

    # Legacy: two junk lines, no header, timestamp in column 0, six sensor
    # readings in columns 2..7 (zero-indexed) — column 1 is skipped.
    try:
        frame = pd.read_csv(path, encoding="utf-8-sig", skiprows=2, header=None, dtype=str)
    except Exception:
        frame = pd.DataFrame()
    if frame.shape[1] >= 8 and len(frame) > 0:
        out = frame.iloc[:, [0, 2, 3, 4, 5, 6, 7]].copy()
        out.columns = ["ts_raw", *SM_COLUMNS]
        return out, "legacy"

    raise AlertError(
        f"The export file {path.name} matches neither known IrroCloud layout "
        "(modern 'Timestamp,SM1..' header, or legacy two-junk-lines). "
        "Stopping rather than guessing — the first five lines are below.",
        evidence_lines=lines,
    )


def load_irrocloud_csv(
    path: Path,
    field_key: str,
    tz_name: str = "America/Boise",
) -> ParseResult:
    """Parse one raw export into tidy per-sensor readings.

    Ported from HELIOS ``load_irrocloud_csv``; see the module docstring for the
    one timezone difference. Raises AlertError (stop-and-alert) on an empty
    file, an unknown layout, unparseable timestamps, or zero usable readings.
    """
    raw, layout = _read_raw_frame(Path(path))
    raw_rows = len(raw)

    ts = pd.to_datetime(raw["ts_raw"], errors="coerce")
    if getattr(ts.dt, "tz", None) is None:
        # Naive timestamps are local time. DST edge cases (the skipped spring
        # hour, the doubled fall hour) become NaT and are dropped — at most two
        # rows a year, and honesty beats inventing an offset.
        ts = ts.dt.tz_localize(ZoneInfo(tz_name), ambiguous="NaT", nonexistent="NaT")
    ts_utc = ts.dt.tz_convert("UTC")

    dropped_no_ts = int(ts_utc.isna().sum())
    if raw_rows > 0 and dropped_no_ts == raw_rows:
        raise AlertError(
            f"No timestamp in {Path(path).name} could be understood "
            f"(example: '{raw['ts_raw'].iloc[0]}'). The site may have changed "
            "its date format.",
            evidence_lines=_first_lines(Path(path), 5),
        )

    values = pd.DataFrame(index=raw.index)
    dropped_sentinel = dropped_negative = clipped = 0
    clipped_masks: dict[str, pd.Series] = {}
    for col in SM_COLUMNS:
        series = pd.to_numeric(raw[col], errors="coerce")
        sentinel_mask = series == SENTINEL_BAD_READING
        dropped_sentinel += int(sentinel_mask.sum())
        series = series.mask(sentinel_mask)
        negative_mask = series < 0
        dropped_negative += int(negative_mask.sum())
        series = series.mask(negative_mask)
        over_mask = series > MAX_TENSION_CB
        clipped += int(over_mask.sum())
        clipped_masks[col] = over_mask.fillna(False)
        values[col] = series.clip(upper=MAX_TENSION_CB)

    keep = ts_utc.notna() & values.notna().any(axis=1)
    dropped_all_null = int((ts_utc.notna() & ~values.notna().any(axis=1)).sum())

    body = values[keep].copy()
    body["ts_utc"] = ts_utc[keep]
    body["ts_raw"] = raw.loc[keep, "ts_raw"].astype(str)
    body = body.sort_values("ts_utc", kind="stable")
    before_dedupe = len(body)
    body = body.drop_duplicates(subset="ts_utc", keep="last")
    dropped_duplicates = before_dedupe - len(body)

    tidy_rows: list[dict] = []
    for col, suffix in zip(SM_COLUMNS, SENSOR_SUFFIXES, strict=True):
        col_values = body[col]
        present = col_values.notna()
        if not present.any():
            continue
        sensor_id = f"{field_key}_{suffix}"
        probe, depth = suffix.split("_")
        clipped_here = clipped_masks[col].reindex(body.index).fillna(False)
        for idx in body.index[present]:
            tidy_rows.append(
                {
                    "sensor_id": sensor_id,
                    "probe": probe,
                    "depth_in": float(depth),
                    "ts_utc": body.at[idx, "ts_utc"],
                    "ts_raw": body.at[idx, "ts_raw"],
                    "tension_cb": float(col_values.at[idx]),
                    "quality_flag": "clipped" if bool(clipped_here.at[idx]) else None,
                }
            )

    readings = pd.DataFrame(tidy_rows, columns=READING_COLUMNS)
    if readings.empty:
        raise AlertError(
            f"The export file {Path(path).name} parsed but contained zero usable "
            "sensor readings (every value was missing, 254, or negative).",
            evidence_lines=_first_lines(Path(path), 5),
        )

    return ParseResult(
        readings=readings,
        layout=layout,
        raw_rows=raw_rows,
        kept_rows=len(body),
        dropped_sentinel_254=dropped_sentinel,
        dropped_negative=dropped_negative,
        clipped_over_240=clipped,
        dropped_no_timestamp=dropped_no_ts,
        dropped_all_null=dropped_all_null,
        dropped_duplicate_ts=dropped_duplicates,
    )


@dataclass
class ValidationReport:
    ok: bool
    problems: list[str] = field(default_factory=list)


def validate_export_file(path: Path) -> ValidationReport:
    """Cheap pre-parse checks (build brief §7): non-empty, known layout.
    ``load_irrocloud_csv`` re-checks everything; this exists so the fetcher can
    fail fast with a clear message before parsing."""
    path = Path(path)
    problems = []
    if not path.exists() or path.stat().st_size == 0:
        problems.append("the downloaded file is empty")
        return ValidationReport(False, problems)
    lines = _first_lines(path, 5)
    if not lines:
        problems.append("the downloaded file could not be read as text")
    elif not lines[0].startswith("Timestamp,") and len(lines) < 3:
        problems.append("the file is too short to be either known layout")
    return ValidationReport(not problems, problems)
