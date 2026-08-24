"""Shared test fixtures. No test touches the network (build brief §14)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SYNTHETIC_DIR = FIXTURES_DIR / "synthetic"

# The synthetic season ends 2026-08-24 06:00 America/Boise (= 12:00 UTC in
# August, MDT is UTC-6). Tests freeze "now" shortly after that so freshness,
# slope windows, and golden emails are deterministic.
FROZEN_NOW_UTC = datetime(2026, 8, 24, 12, 30, tzinfo=UTC)


@pytest.fixture()
def frozen_now():
    return FROZEN_NOW_UTC


@pytest.fixture()
def synthetic_dir() -> Path:
    return SYNTHETIC_DIR
