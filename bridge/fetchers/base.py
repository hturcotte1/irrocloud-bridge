"""The Fetcher protocol every implementation follows."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Protocol

import pandas as pd

from bridge.config import Config, FieldConfig


class Fetcher(Protocol):
    """fetch() returns the path of a raw CSV saved under data/raw/ — saved
    BEFORE parsing, and never deleted (build brief §7)."""

    def fetch(self, field: FieldConfig, start: date, end: date) -> Path: ...

    def close(self) -> None: ...


def raw_export_path(cfg: Config, field_key: str, now_utc) -> Path:
    """data/raw/<field_key>/<YYYY-MM-DD>T<HHMM>.csv (local time in the name so
    the folder reads chronologically for a human)."""
    stamp = pd.Timestamp(now_utc).tz_convert(cfg.tz).strftime("%Y-%m-%dT%H%M")
    path = cfg.raw_dir / field_key / f"{stamp}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
