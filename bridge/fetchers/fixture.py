"""Fixture fetcher: serves the committed synthetic season (offline mode).

The synthetic files end 2026-08-24 06:00 Boise. When the real clock has moved
past that by more than a day, the fetcher shifts every timestamp forward (in
whole hours) so the data ends about two hours before "now" — an offline demo
run months from now still shows a fresh-looking morning instead of a wall of
staleness warnings. When the data is already fresh (tests freeze the
clock; tonight's runs), nothing is shifted, so results stay byte-deterministic.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from bridge.config import Config, FieldConfig
from bridge.errors import AlertError
from bridge.fetchers.base import raw_export_path

TS_FORMAT = "%Y-%m-%d %H:%M:%S"
SHIFT_WHEN_OLDER_THAN = timedelta(hours=26)  # matches the staleness threshold
FRESH_END_OFFSET = timedelta(hours=2)


class FixtureFetcher:
    def __init__(self, cfg: Config, now_utc: datetime | None = None):
        self.cfg = cfg
        self.now_utc = now_utc or datetime.now(UTC)
        self.source_dir = cfg.fixtures_dir / "synthetic"

    def fetch(self, field: FieldConfig, start: date, end: date) -> Path:
        source = self.source_dir / f"{field.key}.csv"
        if not source.exists():
            raise AlertError(
                f"No built-in sample data for field '{field.key}' "
                f"(expected {source})."
            )
        lines = source.read_text(encoding="utf-8").splitlines()

        # Layout detection mirrors the parser: modern has the header line,
        # legacy has two junk lines then data.
        if lines and lines[0].startswith("Timestamp,"):
            header, body = lines[:1], lines[1:]
        else:
            header, body = lines[:2], lines[2:]

        offset = self._shift_offset(body)
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        kept = 0
        for row in csv.reader(body):
            if not row:
                continue
            try:
                stamp = datetime.strptime(row[0], TS_FORMAT) + offset
            except ValueError:
                continue
            if not (start <= stamp.date() <= end):
                continue
            writer.writerow([stamp.strftime(TS_FORMAT), *row[1:]])
            kept += 1
        if kept == 0:
            raise AlertError(
                f"The sample data for '{field.key}' has no rows between {start} "
                f"and {end} — widen the window (fetch --days N)."
            )

        path = raw_export_path(self.cfg, field.key, self.now_utc)
        path.write_text("\n".join(header) + "\n" + out.getvalue(), encoding="utf-8")
        return path

    def _shift_offset(self, body: list[str]) -> timedelta:
        stamps = []
        for row in csv.reader(body):
            if row:
                try:
                    stamps.append(datetime.strptime(row[0], TS_FORMAT))
                except ValueError:
                    continue
        if not stamps:
            return timedelta(0)
        newest = max(stamps)
        now_local = pd.Timestamp(self.now_utc).tz_convert(self.cfg.tz).tz_localize(None)
        age = now_local - newest
        if age <= SHIFT_WHEN_OLDER_THAN:
            return timedelta(0)
        target = (now_local - FRESH_END_OFFSET).replace(minute=0, second=0, microsecond=0)
        hours = int((target - newest).total_seconds() // 3600)
        return timedelta(hours=hours)

    def close(self) -> None:  # nothing to release
        return
