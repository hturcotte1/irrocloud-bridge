"""The bridge's commands.

    python -m bridge.cli setup-check          what is configured, what is missing
    python -m bridge.cli discover             map the real IrroCloud site (browser)
    python -m bridge.cli fetch [--days N]     download + store readings only
    python -m bridge.cli run [--force] [--no-email]   the full morning run
    python -m bridge.cli replay               build reports/season-replay.html
    python -m bridge.cli test-email           send a test message to Henry
    python -m bridge.cli status               show how the last run went

``run`` is the heart: fetch → parse → store → analyze → weather → Helios →
message → notify. Every stage records its outcome in logs/last-run.json, and
no stage failure may ever prevent Henry's status email (build brief §5). The
"commit data/" step lives in the GitHub Actions workflow, not here — see
DECISIONS.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import UTC, datetime, timedelta

import pandas as pd

from bridge import analyze as analyze_mod
from bridge import weather as weather_mod
from bridge.config import Config, load_config
from bridge.errors import AlertError, ConfigError
from bridge.fetchers import make_fetcher
from bridge.helios import LiveHelios, OfflineHelios, run_helios_for_field
from bridge.message import (
    EmailPayload,
    FieldReport,
    compose_jacob_email,
    compose_status_email,
)
from bridge.notify import send_email
from bridge.parse import load_irrocloud_csv
from bridge.store import Store

FETCH_WINDOW_DAYS = 7


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------


def cmd_run(
    cfg: Config,
    force: bool = False,
    no_email: bool = False,
    now_utc: datetime | None = None,
    weather_fn=weather_mod.fetch_forecast,
    fetch_days: int = FETCH_WINDOW_DAYS,
) -> int:
    """The full morning run. Returns the process exit code (0 = OK/WARN)."""
    now_utc = now_utc or datetime.now(UTC)
    now_local = pd.Timestamp(now_utc).tz_convert(cfg.tz)
    run_date_local = now_local.date().isoformat()

    stages: dict[str, dict] = {}
    warnings: list[str] = []
    screenshots: list[str] = []
    reports: list[FieldReport] = []
    jacob_email: EmailPayload | None = None
    jacob_sent_to: str | None = None
    helios_run_ids: list[str] = []
    fields_total = len(cfg.fields)
    field_data: dict[str, dict] = {}  # key -> {"snapshot":…, "helios":…, …}

    store = Store(cfg.db_path)
    run_id = store.start_run(run_date_local)

    def note_alert(prefix: str, exc: AlertError) -> None:
        warnings.append(f"{prefix}: {exc.full_text()}")
        screenshots.extend(exc.screenshots)

    # ---- fetch + parse + store (per field; one failure never stops the rest)
    fetched_ok = 0
    try:
        fetcher = make_fetcher(cfg, now_utc=now_utc)
    except Exception as exc:
        fetcher = None
        stages["fetch"] = {"status": "FAIL", "detail": f"could not start fetcher: {exc}"}
    if fetcher is not None:
        start = (now_local - timedelta(days=fetch_days)).date()
        end = now_local.date()
        new_rows_total = 0
        for field_cfg in cfg.ordered_fields():
            try:
                raw_path = fetcher.fetch(field_cfg, start, end)
                parsed = load_irrocloud_csv(raw_path, field_cfg.key, cfg.timezone_name)
                new_rows = store.insert_readings(
                    field_cfg.key, parsed, cfg.fetcher, raw_path.name
                )
                store.write_readings_csv(
                    field_cfg.key,
                    cfg.readings_dir / f"{field_cfg.key}.csv",
                    cfg.timezone_name,
                )
                field_data[field_cfg.key] = {"parse_summary": parsed.summary()}
                new_rows_total += new_rows
                fetched_ok += 1
            except AlertError as exc:
                note_alert(f"{field_cfg.name} fetch/parse", exc)
            except Exception as exc:  # noqa: BLE001 — one field must not kill the run
                warnings.append(f"{field_cfg.name} fetch/parse crashed: {exc}")
        try:
            fetcher.close()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"fetcher close/logout failed: {exc}")
        status = "OK" if fetched_ok == fields_total else ("WARN" if fetched_ok else "FAIL")
        stages["fetch"] = {
            "status": status,
            "detail": f"{fetched_ok}/{fields_total} fields, {new_rows_total} new readings",
        }

    # ---- analyze (works from the store, so old data still analyzes) --------
    analyzed_ok = 0
    through_ts = None
    for field_cfg in cfg.ordered_fields():
        try:
            readings = store.readings_frame(field_cfg.key)
            snapshot = analyze_mod.analyze_field(field_cfg, readings, now_utc, cfg.tz)
            field_data.setdefault(field_cfg.key, {})["snapshot"] = snapshot
            if snapshot.as_of_utc is not None:
                analyzed_ok += 1
                if through_ts is None or snapshot.as_of_utc > through_ts:
                    through_ts = snapshot.as_of_utc
            if snapshot.primary12_series is not None:
                scored = store.score_forecasts(
                    field_cfg.key, snapshot.primary12_series, now_utc
                )
                if scored:
                    field_data[field_cfg.key]["scored"] = scored
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{field_cfg.name} analysis crashed: {exc}")
    through_text = (
        pd.Timestamp(through_ts).tz_convert(cfg.tz).strftime("%-I:%M %p").lower()
        if through_ts is not None
        else "—"
    )
    stages["analyze"] = {
        "status": "OK" if analyzed_ok == fields_total else ("WARN" if analyzed_ok else "FAIL"),
        "detail": f"{analyzed_ok}/{fields_total} fields have data",
        "through": through_text,
    }

    # ---- weather ----------------------------------------------------------
    rain_ok = 0
    for field_cfg in cfg.ordered_fields():
        rain = None
        try:
            rain = weather_fn(field_cfg.lat, field_cfg.lon, now_utc)
        except Exception as exc:  # noqa: BLE001 — weather may never fail the run
            warnings.append(f"{field_cfg.name} weather lookup crashed: {exc}")
        field_data.setdefault(field_cfg.key, {})["rain"] = rain
        rain_ok += rain is not None
    stages["weather"] = {
        "status": "OK" if rain_ok == fields_total else "WARN",
        "detail": (
            f"{rain_ok}/{fields_total} forecasts from Open-Meteo"
            if rain_ok
            else "unreachable — emails say 'rain forecast unavailable'"
        ),
    }

    # ---- helios -----------------------------------------------------------
    try:
        if cfg.helios_mode == "live":
            session = LiveHelios(cfg.helios_url, cfg.helios_email, cfg.helios_password)
        else:
            session = OfflineHelios()
        setups = session.field_setups(cfg.ordered_fields())
        helios_ok = 0
        for field_cfg in cfg.ordered_fields():
            data = field_data.setdefault(field_cfg.key, {})
            readings = store.readings_frame(field_cfg.key)
            if readings.empty:
                data["helios"] = None
                continue
            result = run_helios_for_field(
                session,
                setups[field_cfg.key],
                readings,
                data.get("rain"),
                now_utc,
                run_date_local,
            )
            data["helios"] = result
            if result.status == "ok":
                helios_ok += 1
                if result.run_id:
                    helios_run_ids.append(result.run_id)
                target = pd.Timestamp(now_utc) + timedelta(hours=24)
                snapshot = data.get("snapshot")
                if result.forecast_24 is not None:
                    store.record_forecast(
                        field_cfg.key, now_utc, target, 24, "helios_model",
                        result.forecast_24,
                    )
                persistence = result.persistence_24
                if persistence is None and snapshot and snapshot.d12.primary is not None:
                    persistence = snapshot.d12.primary
                if persistence is not None:
                    store.record_forecast(
                        field_cfg.key, now_utc, target, 24, "persistence", persistence
                    )
                if (
                    snapshot is not None
                    and snapshot.dry.status == "ok"
                    and snapshot.d12.primary is not None
                ):
                    store.record_forecast(
                        field_cfg.key, now_utc, target, 24, "dry_down",
                        snapshot.d12.primary + snapshot.dry.slope_cb_per_day,
                    )
                if result.save_status in ("skipped", "failed"):
                    warnings.append(
                        f"{field_cfg.name}: Helios run not saved — {result.save_detail}"
                    )
                if result.weather_was_caller_supplied:
                    warnings.append(
                        f"{field_cfg.name}: Helios needed our Open-Meteo weather "
                        "(its own weather source failed)."
                    )
            elif result.error:
                warnings.append(f"{field_cfg.name}: {result.error}")
        detail = f"{helios_ok}/{fields_total} forecasts"
        if cfg.helios_mode == "offline":
            detail += " (offline stub — review gate on)"
        stages["helios"] = {
            "status": "OK" if helios_ok == fields_total else ("WARN" if helios_ok else "FAIL"),
            "detail": detail,
        }
    except AlertError as exc:
        note_alert("Helios", exc)
        stages["helios"] = {"status": "FAIL", "detail": exc.reason}
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Helios stage crashed: {exc}")
        stages["helios"] = {"status": "FAIL", "detail": str(exc)}

    # ---- message ----------------------------------------------------------
    try:
        for field_cfg in cfg.ordered_fields():
            data = field_data.get(field_cfg.key, {})
            snapshot = data.get("snapshot")
            if snapshot is None:
                continue
            yesterday = None
            row = store.forecast_for(field_cfg.key, "helios_model", now_utc)
            if row is not None and row["value_cb"] is not None and row["actual_cb"] is not None:
                yesterday = (row["value_cb"], row["actual_cb"])
            reports.append(
                FieldReport(
                    cfg=field_cfg,
                    snapshot=snapshot,
                    helios=data.get("helios"),
                    rain=data.get("rain"),
                    yesterday=yesterday,
                )
            )
        if reports:
            jacob_email = compose_jacob_email(
                cfg, reports, now_utc, mae=store.forecast_mae(now_utc)
            )
            stages["message"] = {"status": "OK", "detail": "Jacob email composed"}
        else:
            stages["message"] = {"status": "FAIL", "detail": "no field had any data"}
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"message compose crashed: {exc}")
        stages["message"] = {"status": "FAIL", "detail": str(exc)}

    # ---- notify: Jacob's email -------------------------------------------
    if no_email:
        stages["notify"] = {"status": "SKIP", "detail": "--no-email"}
    elif jacob_email is not None:
        try:
            if store.already_sent(run_date_local, "jacob") and not force:
                stages["notify"] = {
                    "status": "SKIP",
                    "detail": "already sent today (use run --force to resend)",
                }
            else:
                if cfg.send_to_jacob and cfg.jacob_email:
                    to, subject = [cfg.jacob_email], jacob_email.subject
                    jacob_sent_to = cfg.jacob_email
                else:
                    to = [cfg.henry_email] if cfg.henry_email else []
                    subject = f"[DRY RUN → Jacob] {jacob_email.subject}"
                outgoing = EmailPayload(
                    subject=subject, text=jacob_email.text, html=jacob_email.html
                )
                where = send_email(cfg, to, outgoing, "jacob-email", now_utc)
                store.record_sent(run_date_local, "jacob", subject)
                stages["notify"] = {"status": "OK", "detail": where}
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"sending Jacob's email failed: {exc}")
            stages["notify"] = {"status": "FAIL", "detail": str(exc)}
    else:
        stages["notify"] = {"status": "SKIP", "detail": "nothing to send"}

    # ---- overall status ---------------------------------------------------
    statuses = [s["status"] for s in stages.values()]
    if any(s == "FAIL" for s in statuses):
        overall = "FAIL"
    elif warnings or any(s == "WARN" for s in statuses):
        overall = "WARN"
    else:
        overall = "OK"

    # ---- status email to Henry — always ----------------------------------
    status_email = compose_status_email(
        cfg,
        status=overall,
        run_date_local=run_date_local,
        now_utc=now_utc,
        stages=stages,
        warnings=warnings,
        jacob_email=jacob_email,
        jacob_was_sent_to=jacob_sent_to,
        fields_ok=analyzed_ok,
        fields_total=fields_total,
        attachments=screenshots,
    )
    status_email_error = None
    if not no_email:
        try:
            to = [cfg.henry_email] if cfg.henry_email else []
            where = send_email(cfg, to, status_email, "status-email", now_utc)
            store.record_sent(run_date_local, "henry", status_email.subject)
            stages["status-email"] = {"status": "OK", "detail": where}
        except Exception as exc:  # noqa: BLE001
            status_email_error = str(exc)
            stages["status-email"] = {"status": "FAIL", "detail": status_email_error}
    else:
        stages["status-email"] = {"status": "SKIP", "detail": "--no-email"}

    # ---- last-run.json + run record ---------------------------------------
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    log_doc = {
        "run_date_local": run_date_local,
        "now_utc": now_utc.isoformat(),
        "status": overall,
        "stages": stages,
        "warnings": warnings,
        "readings_through": through_text,
        "fields_with_data": analyzed_ok,
        "helios_run_ids": helios_run_ids,
        "parse": {
            k: v.get("parse_summary") for k, v in field_data.items() if v.get("parse_summary")
        },
    }
    (cfg.logs_dir / "last-run.json").write_text(
        json.dumps(log_doc, indent=2, default=str) + "\n", encoding="utf-8"
    )
    store.finish_run(
        run_id,
        overall,
        stages,
        jacob_sent_to,
        helios_run_ids,
        notes="; ".join(warnings) if warnings else None,
    )
    store.close()

    print(f"run {overall}: {analyzed_ok}/{fields_total} fields, readings through {through_text}")
    for name, info in stages.items():
        print(f"  {name:<13} {info['status']:<5} {info.get('detail', '')}")
    if status_email_error:
        print(f"STATUS EMAIL FAILED: {status_email_error}", file=sys.stderr)
        return 1
    return 0 if overall in ("OK", "WARN") else 1


# --------------------------------------------------------------------------
# smaller commands
# --------------------------------------------------------------------------


def cmd_setup_check(cfg: Config) -> int:
    print("Modes (from .env; offline-safe defaults when unset):")
    print(f"  FETCHER       = {cfg.fetcher}")
    print(f"  HELIOS_MODE   = {cfg.helios_mode}")
    print(f"  NOTIFY_MODE   = {cfg.notify_mode}")
    print(f"  SEND_TO_JACOB = {str(cfg.send_to_jacob).lower()}")
    missing = cfg.missing_settings()
    if missing:
        print("\nStill needed for these modes (edit .env):")
        for key, why in missing.items():
            print(f"  {key}: {why}")
    else:
        print("\nEverything the current modes need is filled in.")
    placeholders = []
    for f in cfg.fields:
        if f.irrocloud_device_name is None:
            placeholders.append(f"{f.key}: irrocloud_device_name (from the discover run)")
        if any(role == "REPLACE_ME" for role in f.probes.values()):
            placeholders.append(f"{f.key}: which probe letter is the handline probe")
        if f.trigger_is_placeholder:
            placeholders.append(f"{f.key}: trigger_cb (using placeholder 50)")
    if placeholders:
        print("\nStill open in fields.json:")
        for p in placeholders:
            print(f"  {p}")
    return 1 if missing else 0


def cmd_fetch(cfg: Config, days: int) -> int:
    now_utc = datetime.now(UTC)
    now_local = pd.Timestamp(now_utc).tz_convert(cfg.tz)
    start = (now_local - timedelta(days=days)).date()
    end = now_local.date()
    store = Store(cfg.db_path)
    fetcher = make_fetcher(cfg, now_utc=now_utc)
    failures = 0
    try:
        for field_cfg in cfg.ordered_fields():
            try:
                raw_path = fetcher.fetch(field_cfg, start, end)
                parsed = load_irrocloud_csv(raw_path, field_cfg.key, cfg.timezone_name)
                new_rows = store.insert_readings(
                    field_cfg.key, parsed, cfg.fetcher, raw_path.name
                )
                store.write_readings_csv(
                    field_cfg.key,
                    cfg.readings_dir / f"{field_cfg.key}.csv",
                    cfg.timezone_name,
                )
                newest = store.newest_reading_ts(field_cfg.key)
                total = store.reading_counts().get(field_cfg.key, 0)
                print(
                    f"{field_cfg.key}: {new_rows} new readings ({total} total), "
                    f"newest {newest} — {parsed.summary()}"
                )
            except AlertError as exc:
                failures += 1
                print(f"{field_cfg.key}: STOPPED — {exc.full_text()}", file=sys.stderr)
    finally:
        fetcher.close()
        store.close()
    return 1 if failures else 0


def cmd_test_email(cfg: Config) -> int:
    now_utc = datetime.now(UTC)
    payload = EmailPayload(
        subject=f"Bridge test email — {pd.Timestamp(now_utc).tz_convert(cfg.tz):%Y-%m-%d}",
        text=(
            "This is a test message from the IrroCloud → Helios bridge.\n\n"
            "If you are reading it in your inbox, email sending works. "
            "Nothing was fetched and nothing was sent to Jacob."
        ),
    )
    to = [cfg.henry_email] if cfg.henry_email else []
    where = send_email(cfg, to, payload, "test-email", now_utc)
    print(f"test email: {where}")
    return 0


def cmd_status(cfg: Config) -> int:
    path = cfg.logs_dir / "last-run.json"
    if not path.exists():
        print("No run has happened yet (logs/last-run.json is missing).")
        return 1
    doc = json.loads(path.read_text())
    print(
        f"Last run: {doc.get('run_date_local')} — {doc.get('status')} — "
        f"readings through {doc.get('readings_through')}"
    )
    for name, info in doc.get("stages", {}).items():
        print(f"  {name:<13} {info.get('status', '?'):<5} {info.get('detail', '')}")
    for warning in doc.get("warnings", []):
        print(f"  warning: {warning}")
    return 0 if doc.get("status") in ("OK", "WARN") else 1


def cmd_discover(cfg: Config) -> int:
    from bridge.fetchers.discover import run_discovery

    return run_discovery(cfg)


def cmd_replay(cfg: Config) -> int:
    from bridge.replay import build_replay_report

    path = build_replay_report(cfg)
    print(f"wrote {path}")
    return 0


# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bridge.cli", description="IrroCloud → Helios bridge"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup-check", help="show what is configured and what is missing")
    sub.add_parser("discover", help="map the real IrroCloud site (browser, read-only)")
    fetch_p = sub.add_parser("fetch", help="download and store readings only")
    fetch_p.add_argument("--days", type=int, default=FETCH_WINDOW_DAYS)
    run_p = sub.add_parser("run", help="the full morning run")
    run_p.add_argument("--force", action="store_true", help="resend even if already sent today")
    run_p.add_argument("--no-email", action="store_true", help="skip all email sending")
    sub.add_parser("replay", help="build reports/season-replay.html")
    sub.add_parser("test-email", help="send a test message to Henry")
    sub.add_parser("status", help="show how the last run went")
    args = parser.parse_args(argv)

    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"Configuration problem:\n{exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "setup-check":
            return cmd_setup_check(cfg)
        if args.command == "discover":
            return cmd_discover(cfg)
        if args.command == "fetch":
            cfg.require_ready()
            return cmd_fetch(cfg, args.days)
        if args.command == "run":
            cfg.require_ready()
            return cmd_run(cfg, force=args.force, no_email=args.no_email)
        if args.command == "replay":
            return cmd_replay(cfg)
        if args.command == "test-email":
            cfg.require_ready()
            return cmd_test_email(cfg)
        if args.command == "status":
            return cmd_status(cfg)
    except ConfigError as exc:
        print(f"Configuration problem:\n{exc}", file=sys.stderr)
        return 2
    except AlertError as exc:
        print(f"STOPPED (alert): {exc.full_text()}", file=sys.stderr)
        return 3
    except Exception:  # noqa: BLE001 — last-ditch: show the whole story
        traceback.print_exc()
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
