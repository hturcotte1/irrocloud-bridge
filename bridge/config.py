"""Load and validate settings from ``.env`` and ``fields.json``.

Design rules:

- Real environment variables override ``.env`` values. The GitHub Actions
  workflow relies on this to force ``FETCHER=browser``, ``HELIOS_MODE=live``,
  ``NOTIFY_MODE=smtp`` regardless of what a checked-out ``.env`` might say.
- A value of ``REPLACE_ME`` (the placeholder convention across this repo) is
  treated exactly like a missing value, so a half-filled ``.env`` degrades to
  clear "this is not set" errors instead of sending ``REPLACE_ME`` to a website.
- Every error message names the file and the key to fix, in plain English,
  because Henry (not an engineer) is the one who will read it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from bridge.errors import ConfigError

PLACEHOLDER = "REPLACE_ME"

# Jacob has not given his trigger number yet; every email that leans on this
# placeholder says so out loud (build brief §11).
PLACEHOLDER_TRIGGER_CB = 50.0

# The order fields appear in every email and report (build brief §11).
FIELD_ORDER = ["cunningham-6", "rv80", "bennett-1-n", "whitted"]

_MODE_CHOICES = {
    "FETCHER": {"fixture", "browser", "api"},
    "HELIOS_MODE": {"offline", "live"},
    "NOTIFY_MODE": {"file", "smtp"},
}

_KNOWN_KEYS = [
    "FETCHER",
    "HELIOS_MODE",
    "NOTIFY_MODE",
    "SEND_TO_JACOB",
    "IRROCLOUD_URL",
    "IRROCLOUD_EMAIL",
    "IRROCLOUD_PASSWORD",
    "HELIOS_URL",
    "HELIOS_EMAIL",
    "HELIOS_PASSWORD",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "HENRY_EMAIL",
    "JACOB_EMAIL",
    "HENRY_PHONE",
    "TIMEZONE",
]


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a ``KEY=VALUE`` file. Hand-rolled (~20 lines) instead of pulling in
    python-dotenv, to keep the dependency list short for an offline-first tool."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Allow trailing same-line comments only after whitespace, so a
        # password containing '#' is not truncated.
        if " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _clean(value: str | None) -> str | None:
    """Missing, empty, and REPLACE_ME all mean "not set"."""
    if value is None:
        return None
    value = value.strip()
    if not value or value == PLACEHOLDER:
        return None
    return value


@dataclass(frozen=True)
class FieldConfig:
    """One field from ``fields.json``."""

    key: str
    name: str
    crop: str
    acres: float
    acres_note: str | None
    lat: float
    lon: float
    planted: str
    irrocloud_device_name: str | None  # None until the morning discovery fills it in
    probes: dict[str, str]  # probe letter -> "pivot" | "handline" | "REPLACE_ME"
    probes_note: str | None
    trigger_cb: float
    trigger_is_placeholder: bool
    irrocloud_device_id: str | None = None  # the site's numeric device id
    helios_field_key: str | None = None  # the field_key in Jacob's Helios account

    def handline_probes(self) -> set[str]:
        """Probe letters marked handline — shown in emails but excluded from the
        headline (primary-series) numbers. An unconfirmed role (REPLACE_ME)
        counts as pivot until a human says otherwise; see DECISIONS.md."""
        return {letter for letter, role in self.probes.items() if role == "handline"}


@dataclass(frozen=True)
class Config:
    root: Path
    fetcher: str
    helios_mode: str
    notify_mode: str
    send_to_jacob: bool
    irrocloud_url: str
    irrocloud_email: str | None
    irrocloud_password: str | None
    helios_url: str | None
    helios_email: str | None
    helios_password: str | None
    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_password: str | None
    henry_email: str | None
    jacob_email: str | None
    henry_phone: str | None
    timezone_name: str
    fields: list[FieldConfig] = field(default_factory=list)

    # ---- paths ----------------------------------------------------------
    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def readings_dir(self) -> Path:
        return self.data_dir / "readings"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bridge.sqlite"

    @property
    def outbox_dir(self) -> Path:
        return self.root / "outbox"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def screenshots_dir(self) -> Path:
        return self.logs_dir / "screenshots"

    @property
    def discovery_dir(self) -> Path:
        return self.root / "discovery"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def fixtures_dir(self) -> Path:
        return self.root / "tests" / "fixtures"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    # ---- lookups --------------------------------------------------------
    def field_by_key(self, key: str) -> FieldConfig:
        for f in self.fields:
            if f.key == key:
                return f
        raise ConfigError(
            f"No field named '{key}' in fields.json — known fields: "
            + ", ".join(f.key for f in self.fields)
        )

    def ordered_fields(self) -> list[FieldConfig]:
        """Fields in the fixed email order (Cunningham 6 first)."""
        by_key = {f.key: f for f in self.fields}
        ordered = [by_key[k] for k in FIELD_ORDER if k in by_key]
        ordered += [f for f in self.fields if f.key not in FIELD_ORDER]
        return ordered

    # ---- readiness checks ------------------------------------------------
    def missing_settings(self) -> dict[str, str]:
        """Settings the CURRENT modes need but that are not filled in.

        Returns {setting name: plain-English explanation}. Offline modes need
        nothing, which is why a fresh checkout runs with no .env at all.
        """
        missing: dict[str, str] = {}
        if self.fetcher == "browser":
            if not self.irrocloud_email:
                missing["IRROCLOUD_EMAIL"] = "the email used to log into IrroCloud"
            if not self.irrocloud_password:
                missing["IRROCLOUD_PASSWORD"] = "the IrroCloud password"
        if self.helios_mode == "live":
            if not self.helios_url:
                missing["HELIOS_URL"] = "the web address of the Helios app"
            if not self.helios_email:
                missing["HELIOS_EMAIL"] = "Jacob's Helios login email"
            if not self.helios_password:
                missing["HELIOS_PASSWORD"] = "Jacob's Helios password"
        if self.notify_mode == "smtp":
            if not self.smtp_user:
                missing["SMTP_USER"] = "the Gmail address the bridge sends from"
            if not self.smtp_password:
                missing["SMTP_PASSWORD"] = "the 16-character Gmail app password"
            if not self.henry_email:
                missing["HENRY_EMAIL"] = "Henry's email (the status email always goes here)"
        if self.send_to_jacob and not self.jacob_email:
            missing["JACOB_EMAIL"] = "Jacob's email (SEND_TO_JACOB is true but no address is set)"
        return missing

    def require_ready(self) -> None:
        missing = self.missing_settings()
        if missing:
            lines = [
                "Some settings the current modes need are not filled in yet. "
                "Open the .env file and set:"
            ]
            lines += [f"  - {key}: {why}" for key, why in missing.items()]
            lines.append("(.env.example explains every line; NEXT.md lists what to gather.)")
            raise ConfigError("\n".join(lines))


def _load_field(entry: dict, index: int) -> FieldConfig:
    def need(key: str):
        if key not in entry or entry[key] in (None, ""):
            raise ConfigError(
                f"Field #{index + 1} in fields.json is missing '{key}'. "
                "Every field needs key, name, crop, acres, lat, lon, planted, "
                "irrocloud_device_name, probes, and trigger_cb."
            )
        return entry[key]

    probes_raw = need("probes")
    if not isinstance(probes_raw, dict) or not probes_raw:
        raise ConfigError(
            f"Field #{index + 1} in fields.json: 'probes' must map probe letters "
            "to 'pivot' or 'handline', e.g. {\"a\": \"pivot\"}."
        )
    probes: dict[str, str] = {}
    for letter, role in probes_raw.items():
        if role not in ("pivot", "handline", PLACEHOLDER):
            raise ConfigError(
                f"Field '{entry.get('key', index + 1)}' in fields.json: probe "
                f"'{letter}' has role '{role}'. Use 'pivot', 'handline', or "
                f"'{PLACEHOLDER}' (unknown until the morning discovery run)."
            )
        probes[str(letter)] = role

    trigger_raw = entry.get("trigger_cb", PLACEHOLDER)
    if trigger_raw in (PLACEHOLDER, None, ""):
        trigger_cb, trigger_is_placeholder = PLACEHOLDER_TRIGGER_CB, True
    else:
        try:
            trigger_cb, trigger_is_placeholder = float(trigger_raw), False
        except (TypeError, ValueError):
            raise ConfigError(
                f"Field '{entry.get('key', index + 1)}' in fields.json: trigger_cb "
                f"is '{trigger_raw}' but must be a number (centibars) or REPLACE_ME."
            ) from None

    return FieldConfig(
        key=str(need("key")),
        name=str(need("name")),
        crop=str(need("crop")),
        acres=float(need("acres")),
        acres_note=_clean(entry.get("acres_note")),
        lat=float(need("lat")),
        lon=float(need("lon")),
        planted=str(need("planted")),
        irrocloud_device_name=_clean(entry.get("irrocloud_device_name")),
        irrocloud_device_id=_clean(str(entry.get("irrocloud_device_id") or "")),
        helios_field_key=_clean(entry.get("helios_field_key")),
        probes=probes,
        probes_note=_clean(entry.get("probes_note")),
        trigger_cb=trigger_cb,
        trigger_is_placeholder=trigger_is_placeholder,
    )


def load_config(
    root: Path | None = None,
    environ: dict[str, str] | None = None,
    env_file: bool = True,
) -> Config:
    """Build the Config. ``root`` defaults to the repository root (the folder
    containing fields.json); tests pass a temp dir and their own environ.
    ``env_file=False`` skips reading ``root/.env`` — the golden samples use it
    so their output never depends on the operator's local settings."""
    if root is None:
        root = Path(__file__).resolve().parent.parent
    environ = dict(os.environ if environ is None else environ)

    values = _parse_env_file(root / ".env") if env_file else {}
    for key in _KNOWN_KEYS:
        if key in environ and environ[key].strip():
            values[key] = environ[key]

    def get(key: str, default: str | None = None) -> str | None:
        return _clean(values.get(key)) or default

    for key, choices in _MODE_CHOICES.items():
        raw = get(key)
        if raw is not None and raw not in choices:
            raise ConfigError(
                f"{key} is set to '{raw}' but must be one of: "
                + ", ".join(sorted(choices))
                + ". Fix it in the .env file."
            )

    send_raw = (get("SEND_TO_JACOB") or "false").lower()
    if send_raw not in ("true", "false"):
        raise ConfigError(
            f"SEND_TO_JACOB is set to '{send_raw}' but must be exactly true or false. "
            "Fix it in the .env file."
        )

    timezone_name = get("TIMEZONE") or "America/Boise"
    try:
        ZoneInfo(timezone_name)
    except Exception:
        raise ConfigError(
            f"TIMEZONE is set to '{timezone_name}', which is not a known timezone "
            "name. Use America/Boise (or leave the line out)."
        ) from None

    port_raw = get("SMTP_PORT") or "587"
    try:
        smtp_port = int(port_raw)
    except ValueError:
        raise ConfigError(
            f"SMTP_PORT is set to '{port_raw}' but must be a number (usually 587)."
        ) from None

    fields_path = root / "fields.json"
    if not fields_path.exists():
        raise ConfigError(f"fields.json was not found at {fields_path}.")
    try:
        fields_doc = json.loads(fields_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"fields.json is not valid JSON ({exc}). A stray comma or quote is the "
            "usual cause; ask Claude Code to fix it."
        ) from None
    entries = fields_doc.get("fields")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("fields.json must contain a non-empty 'fields' list.")
    fields = [_load_field(entry, i) for i, entry in enumerate(entries)]
    keys = [f.key for f in fields]
    if len(set(keys)) != len(keys):
        raise ConfigError("fields.json has two fields with the same 'key'.")

    return Config(
        root=root,
        fetcher=get("FETCHER") or "fixture",
        helios_mode=get("HELIOS_MODE") or "offline",
        notify_mode=get("NOTIFY_MODE") or "file",
        send_to_jacob=send_raw == "true",
        irrocloud_url=get("IRROCLOUD_URL") or "https://www.irrocloud.com",
        irrocloud_email=get("IRROCLOUD_EMAIL"),
        irrocloud_password=get("IRROCLOUD_PASSWORD"),
        helios_url=get("HELIOS_URL"),
        helios_email=get("HELIOS_EMAIL"),
        helios_password=get("HELIOS_PASSWORD"),
        smtp_host=get("SMTP_HOST") or "smtp.gmail.com",
        smtp_port=smtp_port,
        smtp_user=get("SMTP_USER"),
        smtp_password=get("SMTP_PASSWORD"),
        henry_email=get("HENRY_EMAIL"),
        jacob_email=get("JACOB_EMAIL"),
        henry_phone=get("HENRY_PHONE"),
        timezone_name=fields_doc.get("timezone") or timezone_name,
        fields=fields,
    )
