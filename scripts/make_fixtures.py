"""Generate the synthetic IrroCloud export fixtures in tests/fixtures/synthetic/.

Deterministic (fixed seed, fixed end time 2026-08-24 06:00 America/Boise) so the
committed files never churn. Each field exercises different paths:

- cunningham-6  modern layout, 40 days, clean — the ">=30 days hourly" field for
                slope / reset / scorecard / replay tests; ends mid dry-down.
- rv80          modern layout with an extra Battery column; 254 sentinels,
                negative values, duplicated timestamps, and a probe stuck high
                for three days (the MAD outlier case).
- bennett-1-n   LEGACY layout (two junk lines, no header, battery in column 1);
                a fresh irrigation ~10 hours before the end (reset < 24 h).
- whitted       modern layout; a > 3 h gap, a ceiling artifact (all probes at
                225+ then a > 100 cb glitch drop), raw values above 240 (clip),
                and it ends past the placeholder trigger of 50.

Run:  python scripts/make_fixtures.py
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

OUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "synthetic"
END_LOCAL = datetime(2026, 8, 24, 6, 0)  # naive local time, America/Boise
PROBE_OFFSET_12 = {"a": 0.0, "b": -1.5, "c": 2.0}
PROBE_OFFSET_18 = {"a": 0.0, "b": -0.8, "c": 1.0}

FIELDS = {
    "cunningham-6": dict(
        layout="modern",
        days=40,
        base12=12.0,
        rate12=3.2,
        base18=8.0,
        lag18_h=4,
        irrigations_days_before_end=[40, 31, 22, 13.5, 8],
        seed=101,
    ),
    "rv80": dict(
        layout="modern",
        extra_battery_column=True,
        days=35,
        base12=11.0,
        rate12=3.0,
        base18=7.0,
        lag18_h=5,
        irrigations_days_before_end=[35, 26, 17, 8],
        seed=202,
        stuck_probe=("c", 12, 185.0, 20.0, 17.0),  # probe, depth, value, from/to days before end
        sentinel_254_count=30,
        negative_count=3,
        duplicate_tail_rows=5,
    ),
    "bennett-1-n": dict(
        layout="legacy",
        days=36,
        base12=13.0,
        rate12=4.0,
        base18=9.0,
        lag18_h=3,
        irrigations_days_before_end=[36, 24, 12, 10 / 24],
        seed=303,
        sentinel_254_count=12,
    ),
    "whitted": dict(
        layout="modern",
        days=38,
        base12=12.0,
        rate12=3.5,
        base18=8.0,
        lag18_h=6,
        irrigations_days_before_end=[38, 30, 14],
        seed=404,
        gap_from_to_days_before_end=(20.0, 19.75),  # ~6 hours removed
        artifact_ramp_days_before_end=(28.0, 25.0),  # climb to ceiling, then glitch drop
        seed_over_240=True,
    ),
}


def _series_for_field(cfg: dict) -> tuple[list[datetime], dict[tuple[str, int], list[float]]]:
    rng = np.random.default_rng(cfg["seed"])
    start = END_LOCAL - timedelta(days=cfg["days"])
    stamps = [start + timedelta(hours=h) for h in range(cfg["days"] * 24 + 1)]
    irrigations = sorted(
        END_LOCAL - timedelta(days=float(d)) for d in cfg["irrigations_days_before_end"]
    )
    rate18 = cfg["rate12"] * 0.55
    lag = timedelta(hours=cfg["lag18_h"])

    def last_event_before(t: datetime, events: list[datetime]) -> datetime:
        prior = [e for e in events if e <= t]
        return prior[-1] if prior else stamps[0]

    # A center pivot takes hours to cross a field, so each probe location gets
    # wet at a different time. This staggering matters: simultaneous resets on
    # every probe would (correctly) trip HELIOS's "every probe dropped at once"
    # artifact rule, and no synthetic irrigation would ever count as real.
    probe_lag_h = {"a": 0, "b": 1, "c": 2}

    values: dict[tuple[str, int], list[float]] = {
        (p, d): [] for p in "abc" for d in (12, 18)
    }
    for t in stamps:
        diurnal = 1.2 * math.sin(2 * math.pi * (t.hour - 15) / 24.0)
        for p in "abc":
            wet12 = [e + timedelta(hours=probe_lag_h[p]) for e in irrigations]
            wet18 = [e + lag + timedelta(hours=probe_lag_h[p]) for e in irrigations]
            since12 = (t - last_event_before(t, wet12)).total_seconds() / 86400.0
            since18 = (t - last_event_before(t, wet18)).total_seconds() / 86400.0
            v12 = (
                cfg["base12"]
                + cfg["rate12"] * since12
                + diurnal
                + PROBE_OFFSET_12[p]
                + rng.normal(0, 0.4)
            )
            v18 = (
                cfg["base18"]
                + rate18 * since18
                + PROBE_OFFSET_18[p]
                + rng.normal(0, 0.25)
            )
            values[(p, 12)].append(max(v12, 1.0))
            values[(p, 18)].append(max(v18, 1.0))

    # Ceiling artifact (whitted): every probe ramps to 225+ then glitch-drops
    # back to the underlying trajectory in one step — both artifact rules
    # (prev >= 225, drop > 100) and "every probe dropped at once" fire.
    if "artifact_ramp_days_before_end" in cfg:
        hi_d, lo_d = cfg["artifact_ramp_days_before_end"]
        t_hi = END_LOCAL - timedelta(days=hi_d)
        t_lo = END_LOCAL - timedelta(days=lo_d)
        for i, t in enumerate(stamps):
            if t_hi <= t < t_lo:
                frac = (t - t_hi) / (t_lo - t_hi)
                boost = 210.0 * frac
                for p in "abc":
                    values[(p, 12)][i] += boost
                # One probe overshoots 240 near the top → exercises clipping.
                if cfg.get("seed_over_240") and frac > 0.9:
                    values[("c", 12)][i] = 243.0

    # Stuck-high probe (rv80): the MAD outlier the primary selection must drop.
    if "stuck_probe" in cfg:
        p, d, stuck_value, from_d, to_d = cfg["stuck_probe"]
        t_from = END_LOCAL - timedelta(days=from_d)
        t_to = END_LOCAL - timedelta(days=to_d)
        for i, t in enumerate(stamps):
            if t_from <= t < t_to:
                values[(p, d)][i] = stuck_value + float(np.sin(i) * 0.5)

    return stamps, values


def _rows_for_field(cfg: dict) -> list[tuple[str, list[float | None]]]:
    stamps, values = _series_for_field(cfg)
    rng = np.random.default_rng(cfg["seed"] + 1)
    order = [("a", 12), ("a", 18), ("b", 12), ("b", 18), ("c", 12), ("c", 18)]
    rows: list[tuple[str, list[float | None]]] = []
    for i, t in enumerate(stamps):
        rows.append((t.strftime("%Y-%m-%d %H:%M:%S"), [round(values[k][i]) for k in order]))

    # A gap over three hours (breaks dry-down continuity in analysis).
    if "gap_from_to_days_before_end" in cfg:
        from_d, to_d = cfg["gap_from_to_days_before_end"]
        t_from = END_LOCAL - timedelta(days=from_d)
        t_to = END_LOCAL - timedelta(days=to_d)
        rows = [
            r
            for r, t in zip(rows, stamps, strict=True)
            if not (t_from <= t < t_to)
        ]
        stamps = [t for t in stamps if not (t_from <= t < t_to)]

    # 254 sentinels (bad/disconnected sensor) sprinkled on probe b 18" (SM4).
    n254 = cfg.get("sentinel_254_count", 0)
    if n254:
        picks = rng.choice(len(rows), size=min(n254, len(rows)), replace=False)
        for i in picks:
            rows[i][1][3] = 254
    # A few negative glitches on probe a 18" (SM2).
    for i in range(cfg.get("negative_count", 0)):
        rows[10 + i * 7][1][1] = -1

    # Duplicated timestamps at the tail: the file repeats its last rows with
    # slightly different values; the parser must keep the LAST occurrence.
    ndup = cfg.get("duplicate_tail_rows", 0)
    if ndup:
        for ts, vals in list(rows[-ndup:]):
            rows.append((ts, [None if v is None else v + 1 for v in vals]))

    return rows


def _write_modern(path: Path, rows: list, battery: bool) -> None:
    header = "Timestamp,SM1,SM2,SM3,SM4,SM5,SM6" + (",Battery" if battery else "")
    lines = [header]
    for ts, vals in rows:
        cells = [ts] + ["" if v is None else str(int(v)) for v in vals]
        if battery:
            cells.append("3.9")
        lines.append(",".join(cells))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_legacy(path: Path, rows: list) -> None:
    # Two junk lines, then: timestamp, battery, six sensor readings (cols 2..7).
    lines = [
        "IRROMETER IrroCloud data export",
        "Device: BENNETT-1N-IC10  Generated: 2026-08-24 06:05",
    ]
    for ts, vals in rows:
        cells = [ts, "3.8"] + ["" if v is None else str(int(v)) for v in vals]
        lines.append(",".join(cells))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for field_key, cfg in FIELDS.items():
        rows = _rows_for_field(cfg)
        path = OUT_DIR / f"{field_key}.csv"
        if cfg["layout"] == "modern":
            _write_modern(path, rows, cfg.get("extra_battery_column", False))
        else:
            _write_legacy(path, rows)
        print(f"{path.name}: {len(rows)} data rows")

    # A third, unknown layout — the parser must stop and alert on this.
    bad = OUT_DIR / "bad_layout.csv"
    bad.write_text(
        "Export Report\nGenerated for grower\nDate;Reading\n2026-07-01;41\n2026-07-02;44\n",
        encoding="utf-8",
    )
    print(f"{bad.name}: intentionally unrecognizable")


if __name__ == "__main__":
    main()
