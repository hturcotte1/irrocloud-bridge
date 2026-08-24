"""SQLite persistence. Idempotent everywhere.

The database file lives in ``data/bridge.sqlite`` and is committed back to the
repository by the morning workflow — GitHub Actions machines are wiped between
runs, so the repo itself is the bridge's memory (readings, forecasts, what was
sent to whom). Every insert is safe to repeat: readings are UNIQUE per
(sensor_id, timestamp), forecasts upsert on their natural key, and the sent log
is what stops a second run the same day from re-emailing Jacob.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from bridge.parse import READING_COLUMNS, ParseResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    field_key   TEXT NOT NULL,
    sensor_id   TEXT NOT NULL,
    probe       TEXT NOT NULL,
    depth_in    REAL NOT NULL,
    ts_utc      TEXT NOT NULL,
    ts_raw      TEXT,
    tension_cb  REAL NOT NULL,
    quality_flag TEXT,
    source      TEXT NOT NULL,
    export_file TEXT,
    inserted_at TEXT NOT NULL,
    UNIQUE (sensor_id, ts_utc)
);
CREATE INDEX IF NOT EXISTS idx_readings_field_ts ON readings (field_key, ts_utc);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date_local  TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT,
    stage_status_json TEXT,
    jacob_email_sent_to TEXT,
    helios_run_ids_json TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS forecasts (
    field_key  TEXT NOT NULL,
    made_at    TEXT NOT NULL,
    target_ts  TEXT NOT NULL,
    horizon_h  INTEGER NOT NULL,
    source     TEXT NOT NULL,   -- helios_model | persistence | dry_down
    value_cb   REAL,
    actual_cb  REAL,
    scored_at  TEXT,
    UNIQUE (field_key, target_ts, horizon_h, source)
);

CREATE TABLE IF NOT EXISTS sent_log (
    run_date_local TEXT NOT NULL,
    recipient      TEXT NOT NULL,   -- logical: 'jacob' | 'henry'
    sent_at        TEXT NOT NULL,
    subject        TEXT NOT NULL
);
"""


def _iso(ts) -> str:
    """Store every instant as an ISO-8601 UTC string so rows are readable in
    any SQLite browser and sort lexicographically in time order."""
    if isinstance(ts, str):
        return ts
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is None:
        raise ValueError(f"naive timestamp {ts!r} — the bridge stores UTC only")
    return stamp.tz_convert("UTC").isoformat()


class Store:
    """All database access goes through this class."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- readings -------------------------------------------------------
    def insert_readings(
        self,
        field_key: str,
        parsed: ParseResult | pd.DataFrame,
        source: str,
        export_file: str | None,
    ) -> int:
        """Insert tidy readings; duplicates (same sensor, same instant) are
        ignored so re-fetching an overlapping window is free. Returns the
        number of genuinely new rows."""
        frame = parsed.readings if isinstance(parsed, ParseResult) else parsed
        now = datetime.now(UTC).isoformat()
        rows = [
            (
                field_key,
                r.sensor_id,
                r.probe,
                float(r.depth_in),
                _iso(r.ts_utc),
                str(r.ts_raw),
                float(r.tension_cb),
                r.quality_flag,
                source,
                export_file,
                now,
            )
            for r in frame.itertuples(index=False)
        ]
        before = self.conn.total_changes
        self.conn.executemany(
            "INSERT OR IGNORE INTO readings "
            "(field_key, sensor_id, probe, depth_in, ts_utc, ts_raw, tension_cb, "
            " quality_flag, source, export_file, inserted_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()
        return self.conn.total_changes - before

    def readings_frame(
        self,
        field_key: str,
        since_utc: datetime | None = None,
        until_utc: datetime | None = None,
    ) -> pd.DataFrame:
        """Readings for one field as a tidy DataFrame (ts_utc tz-aware)."""
        query = "SELECT * FROM readings WHERE field_key = ?"
        params: list = [field_key]
        if since_utc is not None:
            query += " AND ts_utc >= ?"
            params.append(_iso(since_utc))
        if until_utc is not None:
            query += " AND ts_utc <= ?"
            params.append(_iso(until_utc))
        query += " ORDER BY ts_utc, sensor_id"
        frame = pd.read_sql_query(query, self.conn, params=params)
        if not frame.empty:
            frame["ts_utc"] = pd.to_datetime(frame["ts_utc"], utc=True, format="ISO8601")
        else:
            frame = pd.DataFrame(columns=[*READING_COLUMNS, "field_key"])
        return frame

    def newest_reading_ts(self, field_key: str) -> pd.Timestamp | None:
        row = self.conn.execute(
            "SELECT MAX(ts_utc) AS m FROM readings WHERE field_key = ?", (field_key,)
        ).fetchone()
        return pd.Timestamp(row["m"]) if row and row["m"] else None

    def reading_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT field_key, COUNT(*) AS n FROM readings GROUP BY field_key"
        ).fetchall()
        return {r["field_key"]: r["n"] for r in rows}

    def write_readings_csv(self, field_key: str, out_path: Path, tz_name: str) -> int:
        """Write the tidy history CSV (data/readings/<field>.csv) so the data is
        readable on GitHub without tools. Returns the row count."""
        frame = self.readings_frame(field_key)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if frame.empty:
            out_path.write_text(
                "ts_utc,ts_local,sensor_id,probe,depth_in,tension_cb,quality_flag\n"
            )
            return 0
        out = pd.DataFrame(
            {
                "ts_utc": frame["ts_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ts_local": frame["ts_utc"]
                .dt.tz_convert(tz_name)
                .dt.strftime("%Y-%m-%d %H:%M %Z"),
                "sensor_id": frame["sensor_id"],
                "probe": frame["probe"],
                "depth_in": frame["depth_in"],
                "tension_cb": frame["tension_cb"],
                "quality_flag": frame["quality_flag"],
            }
        )
        out.to_csv(out_path, index=False)
        return len(out)

    # ---- runs -----------------------------------------------------------
    def start_run(self, run_date_local: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (run_date_local, started_at) VALUES (?, ?)",
            (run_date_local, datetime.now(UTC).isoformat()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(
        self,
        run_id: int,
        status: str,
        stage_status: dict,
        jacob_email_sent_to: str | None = None,
        helios_run_ids: list[str] | None = None,
        notes: str | None = None,
    ) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at=?, status=?, stage_status_json=?, "
            "jacob_email_sent_to=?, helios_run_ids_json=?, notes=? WHERE id=?",
            (
                datetime.now(UTC).isoformat(),
                status,
                json.dumps(stage_status),
                jacob_email_sent_to,
                json.dumps(helios_run_ids or []),
                notes,
                run_id,
            ),
        )
        self.conn.commit()

    # ---- forecasts / scorecard -----------------------------------------
    def record_forecast(
        self,
        field_key: str,
        made_at: datetime,
        target_ts: datetime,
        horizon_h: int,
        source: str,
        value_cb: float | None,
    ) -> None:
        """Upsert on the natural key so a same-day re-run updates rather than
        duplicates. A later made_at wins (freshest run of the day)."""
        self.conn.execute(
            "INSERT INTO forecasts (field_key, made_at, target_ts, horizon_h, source, value_cb) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT (field_key, target_ts, horizon_h, source) "
            "DO UPDATE SET made_at=excluded.made_at, value_cb=excluded.value_cb "
            "WHERE excluded.made_at >= forecasts.made_at",
            (field_key, _iso(made_at), _iso(target_ts), horizon_h, source, value_cb),
        )
        self.conn.commit()

    def score_forecasts(
        self,
        field_key: str,
        primary_series: pd.Series,
        now_utc: datetime,
        tolerance: timedelta = timedelta(minutes=90),
    ) -> int:
        """Fill actual_cb for forecasts whose target time has passed, using the
        primary 12" series (forecasts are for the primary zone). An actual must
        lie within ±90 minutes of the target. Returns how many were scored."""
        if primary_series is None or primary_series.empty:
            return 0
        rows = self.conn.execute(
            "SELECT rowid, target_ts FROM forecasts "
            "WHERE field_key=? AND actual_cb IS NULL AND target_ts <= ?",
            (field_key, _iso(now_utc)),
        ).fetchall()
        series = primary_series.sort_index()
        scored = 0
        for row in rows:
            target = pd.Timestamp(row["target_ts"])
            window = series[
                (series.index >= target - tolerance) & (series.index <= target + tolerance)
            ]
            if window.empty:
                continue
            deltas = pd.Series((window.index - target).abs(), index=window.index)
            nearest_idx = deltas.idxmin()
            self.conn.execute(
                "UPDATE forecasts SET actual_cb=?, scored_at=? WHERE rowid=?",
                (float(window[nearest_idx]), datetime.now(UTC).isoformat(), row["rowid"]),
            )
            scored += 1
        self.conn.commit()
        return scored

    def forecast_mae(
        self, now_utc: datetime, field_key: str | None = None, days: int = 14
    ) -> dict[str, float]:
        """Running mean absolute error per source over the last ``days`` days.
        Only reported once at least three scored points exist for a source."""
        query = (
            "SELECT source, COUNT(*) AS n, AVG(ABS(value_cb - actual_cb)) AS mae "
            "FROM forecasts WHERE actual_cb IS NOT NULL AND value_cb IS NOT NULL "
            "AND target_ts >= ?"
        )
        params: list = [_iso(pd.Timestamp(now_utc) - timedelta(days=days))]
        if field_key:
            query += " AND field_key = ?"
            params.append(field_key)
        query += " GROUP BY source"
        rows = self.conn.execute(query, params).fetchall()
        return {r["source"]: round(r["mae"], 2) for r in rows if r["n"] >= 3}

    def forecast_for(
        self, field_key: str, source: str, target_near: datetime, tolerance_h: float = 3.0
    ) -> sqlite3.Row | None:
        """The stored forecast (if any) whose target is near a given instant —
        used for the "yesterday it said X; actual was Y" email line."""
        rows = self.conn.execute(
            "SELECT * FROM forecasts WHERE field_key=? AND source=? "
            "AND target_ts BETWEEN ? AND ? ORDER BY target_ts DESC",
            (
                field_key,
                source,
                _iso(pd.Timestamp(target_near) - timedelta(hours=tolerance_h)),
                _iso(pd.Timestamp(target_near) + timedelta(hours=tolerance_h)),
            ),
        ).fetchall()
        return rows[0] if rows else None

    # ---- sent log -------------------------------------------------------
    def already_sent(self, run_date_local: str, recipient: str) -> bool:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM sent_log WHERE run_date_local=? AND recipient=?",
            (run_date_local, recipient),
        ).fetchone()
        return row["n"] > 0

    def record_sent(self, run_date_local: str, recipient: str, subject: str) -> None:
        self.conn.execute(
            "INSERT INTO sent_log (run_date_local, recipient, sent_at, subject) "
            "VALUES (?,?,?,?)",
            (run_date_local, recipient, datetime.now(UTC).isoformat(), subject),
        )
        self.conn.commit()
