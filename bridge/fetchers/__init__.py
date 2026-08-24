"""Pluggable fetchers: where raw IrroCloud CSVs come from.

One interface (``bridge.fetchers.base.Fetcher``), three implementations:

- ``fixture``  — built-in synthetic season data; the offline default.
- ``browser``  — Playwright against the real IrroCloud website.
- ``api``      — a stub waiting for Irrometer's API documentation; when that
                 arrives, only this layer changes (build brief §1).
"""

from __future__ import annotations

from bridge.config import Config
from bridge.errors import ConfigError
from bridge.fetchers.base import Fetcher, raw_export_path

__all__ = ["Fetcher", "make_fetcher", "raw_export_path"]


def make_fetcher(cfg: Config, now_utc=None) -> Fetcher:
    """Build the fetcher the FETCHER mode asks for. Browser bits import lazily
    so offline mode works on a machine without Playwright browsers."""
    if cfg.fetcher == "fixture":
        from bridge.fetchers.fixture import FixtureFetcher

        return FixtureFetcher(cfg, now_utc=now_utc)
    if cfg.fetcher == "browser":
        from bridge.fetchers.irrocloud_browser import IrroCloudBrowserFetcher

        return IrroCloudBrowserFetcher(cfg)
    if cfg.fetcher == "api":
        from bridge.fetchers.irrocloud_api import IrroCloudApiFetcher

        return IrroCloudApiFetcher(cfg)
    raise ConfigError(f"Unknown FETCHER '{cfg.fetcher}' — use fixture, browser, or api.")
