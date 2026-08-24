"""Placeholder for Irrometer's official data API.

Irrigant has asked Irrometer for the API access their website advertises. When
the documentation arrives, ONLY this file changes (build brief §1): implement
``fetch()`` to call their API and write a CSV in one of the two known layouts
(or extend bridge/parse.py with the API's shape), and set ``FETCHER=api`` in
the .env file. Everything downstream — parsing, storage, analysis, emails —
stays exactly as it is.

Henry: when the API docs arrive, open a Claude Code session on this repository
and say "implement fetchers/irrocloud_api.py from these docs", pasting them in.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from bridge.config import Config, FieldConfig
from bridge.errors import ConfigError


class IrroCloudApiFetcher:
    def __init__(self, cfg: Config):
        raise ConfigError(
            "FETCHER=api is a placeholder — Irrometer has not sent API "
            "documentation yet. Use FETCHER=browser (the website) or "
            "FETCHER=fixture (sample data). When the docs arrive, open Claude "
            "Code on this repository and say: implement "
            "fetchers/irrocloud_api.py from these docs."
        )

    def fetch(self, field: FieldConfig, start: date, end: date) -> Path:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError
