"""Season-replay report: one self-contained HTML file, inline SVG, printable.

`python -m bridge.cli replay` → reports/season-replay.html. One section per
field: the tension chart (all probes at both depths, primary series bold, gaps
honest), wetting-event markers labeled per the HELIOS rules, an events table
(drop size, how long the 18" depth took to respond, the dry-down rate that
followed), and a hindsight table: what the days-to-trigger arithmetic would
have said each morning versus when the crossing actually happened.

Data comes from the store when it has readings (the real season after
back-fill), else from the committed synthetic fixtures — so the report can be
built and reviewed before any real data exists. Charts follow the validated
reference palette (see the dataviz method): identity by hue, status colors +
distinct marker shapes for events (never color alone — the tables carry the
labels), single light look because the file is meant to be printed.
"""

from __future__ import annotations

import html as html_lib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from bridge import weather as weather_mod
from bridge.analyze import (
    MAX_GAP,
    WettingEvent,
    detect_wetting_events,
    dry_down_rate,
    pivot_depth,
    primary_frame,
    trigger_outlook,
)
from bridge.config import Config, FieldConfig
from bridge.parse import load_irrocloud_csv
from bridge.store import Store

# Reference palette (dataviz skill) — light mode, print-friendly.
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SURFACE = "#fcfcfb"
PAGE = "#f9f9f7"
SERIES_PRIMARY = "#2a78d6"  # slot 1 blue — the bold primary 12" line
SERIES_18 = "#1baf7a"  # slot 3 aqua — the 18" depth
STATUS = {"irrigation": "#0ca30c", "ambiguous": "#fab219", "artifact": "#d03b3b"}

WIDTH, HEIGHT = 880, 280
MARGIN = {"left": 48, "right": 14, "top": 18, "bottom": 30}


def _x_scale(ts, t0, t1):
    span = max((t1 - t0).total_seconds(), 1.0)
    inner = WIDTH - MARGIN["left"] - MARGIN["right"]
    return MARGIN["left"] + inner * (ts - t0).total_seconds() / span


def _y_scale(value, y_max):
    inner = HEIGHT - MARGIN["top"] - MARGIN["bottom"]
    return MARGIN["top"] + inner * (1 - value / y_max)


def _path(series: pd.Series, t0, t1, y_max) -> str:
    """SVG path with a break wherever readings gap more than three hours —
    the chart must show missing data as missing, not draw through it."""
    parts, prev_ts = [], None
    for ts, value in series.dropna().items():
        cmd = "M" if prev_ts is None or (ts - prev_ts) > MAX_GAP else "L"
        parts.append(
            f"{cmd}{_x_scale(ts, t0, t1):.1f},{_y_scale(float(value), y_max):.1f}"
        )
        prev_ts = ts
    return " ".join(parts)


def _marker(event: WettingEvent, t0, t1, y_max) -> str:
    """Distinct shape per label (color is never the only channel):
    irrigation ▼, ambiguous ●, artifact ✕."""
    x = _x_scale(event.ts, t0, t1)
    y = _y_scale(float(event.prev_value), y_max) - 8
    color = STATUS[event.label]
    if event.label == "irrigation":
        return (
            f'<path d="M{x - 5:.1f},{y - 8:.1f} L{x + 5:.1f},{y - 8:.1f} '
            f'L{x:.1f},{y:.1f} Z" fill="{color}"/>'
        )
    if event.label == "ambiguous":
        return f'<circle cx="{x:.1f}" cy="{y - 4:.1f}" r="4.5" fill="{color}"/>'
    return (
        f'<g stroke="{color}" stroke-width="2.2" stroke-linecap="round">'
        f'<line x1="{x - 4:.1f}" y1="{y - 8:.1f}" x2="{x + 4:.1f}" y2="{y:.1f}"/>'
        f'<line x1="{x - 4:.1f}" y1="{y:.1f}" x2="{x + 4:.1f}" y2="{y - 8:.1f}"/></g>'
    )


def _svg_chart(field_cfg, wide12, wide18, prim12, events, tz, trigger) -> str:
    all_values = pd.concat(
        [frame.stack() for frame in (wide12, wide18) if not frame.empty]
    )
    if all_values.empty:
        return "<p>No readings to chart.</p>"
    t0 = min(f.index.min() for f in (wide12, wide18) if not f.empty)
    t1 = max(f.index.max() for f in (wide12, wide18) if not f.empty)
    y_top = max(60.0, float(all_values.max()), trigger + 10)
    y_max = float(min(245, (int(y_top // 20) + 1) * 20))

    svg = [
        f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
        f'aria-label="Soil tension, {html_lib.escape(field_cfg.name)}" '
        f'style="width:100%;height:auto;background:{SURFACE};'
        'border-radius:6px">'
    ]

    # Gridlines + y labels.
    step = 20 if y_max <= 120 else 60
    value = step
    while value < y_max:
        y = _y_scale(value, y_max)
        svg.append(
            f'<line x1="{MARGIN["left"]}" y1="{y:.1f}" '
            f'x2="{WIDTH - MARGIN["right"]}" y2="{y:.1f}" stroke="{GRID}"/>'
        )
        svg.append(
            f'<text x="{MARGIN["left"] - 6}" y="{y + 3.5:.1f}" text-anchor="end" '
            f'font-size="11" fill="{MUTED}">{value:.0f}</text>'
        )
        value += step
    base_y = _y_scale(0, y_max)
    svg.append(
        f'<line x1="{MARGIN["left"]}" y1="{base_y:.1f}" '
        f'x2="{WIDTH - MARGIN["right"]}" y2="{base_y:.1f}" stroke="{BASELINE}"/>'
    )

    # X ticks: weekly.
    tick = t0.normalize()
    while tick <= t1:
        if tick >= t0:
            x = _x_scale(tick, t0, t1)
            svg.append(
                f'<line x1="{x:.1f}" y1="{base_y:.1f}" x2="{x:.1f}" '
                f'y2="{base_y + 4:.1f}" stroke="{BASELINE}"/>'
            )
            svg.append(
                f'<text x="{x:.1f}" y="{base_y + 16:.1f}" text-anchor="middle" '
                f'font-size="11" fill="{MUTED}">'
                f"{tick.tz_convert(tz).strftime('%b %-d')}</text>"
            )
        tick += timedelta(days=7)

    # Trigger reference line.
    if trigger < y_max:
        y = _y_scale(trigger, y_max)
        svg.append(
            f'<line x1="{MARGIN["left"]}" y1="{y:.1f}" '
            f'x2="{WIDTH - MARGIN["right"]}" y2="{y:.1f}" stroke="{INK_2}" '
            'stroke-dasharray="6 4" stroke-width="1"/>'
        )
        svg.append(
            f'<text x="{WIDTH - MARGIN["right"] - 4}" y="{y - 4:.1f}" '
            f'text-anchor="end" font-size="11" fill="{INK_2}">'
            f"trigger {trigger:.0f} cb</text>"
        )

    # Context lines: other 12" probes (muted), 18" probes (aqua, thin).
    for column in wide12.columns:
        svg.append(
            f'<path d="{_path(wide12[column], t0, t1, y_max)}" fill="none" '
            f'stroke="{MUTED}" stroke-width="1" opacity="0.55"/>'
        )
    for column in wide18.columns:
        svg.append(
            f'<path d="{_path(wide18[column], t0, t1, y_max)}" fill="none" '
            f'stroke="{SERIES_18}" stroke-width="1.2" opacity="0.8"/>'
        )
    # The headline: primary 12" series, bold.
    if not prim12.empty:
        svg.append(
            f'<path d="{_path(prim12["primary"], t0, t1, y_max)}" fill="none" '
            f'stroke="{SERIES_PRIMARY}" stroke-width="2.4"/>'
        )
    for event in events:
        svg.append(_marker(event, t0, t1, y_max))

    svg.append("</svg>")
    return "".join(svg)


def _legend() -> str:
    def chip(color, label, shape="line"):
        if shape == "line":
            mark = (
                f'<span style="display:inline-block;width:18px;height:0;'
                f"border-top:3px solid {color};vertical-align:middle;"
                'margin-right:6px"></span>'
            )
        else:
            mark = (
                f'<span style="display:inline-block;width:10px;height:10px;'
                f'background:{color};border-radius:{"50%" if shape == "dot" else "2px"};'
                'vertical-align:middle;margin-right:6px"></span>'
            )
        return (
            f'<span style="margin-right:18px;color:{INK_2};font-size:13px">'
            f"{mark}{label}</span>"
        )

    return (
        '<p style="margin:6px 0 2px">'
        + chip(SERIES_PRIMARY, 'primary 12" (driest healthy probe)')
        + chip(MUTED, 'other 12" probes')
        + chip(SERIES_18, '18" probes')
        + chip(STATUS["irrigation"], "▼ irrigation", "dot")
        + chip(STATUS["ambiguous"], "● ambiguous (rain that day)", "dot")
        + chip(STATUS["artifact"], "✕ artifact (sensor glitch)", "dot")
        + "</p>"
    )


def _hours_to_18_response(event: WettingEvent, prim18: pd.Series) -> str:
    """Hours until the 18" primary first dropped ≥ 5 cb after the event."""
    if prim18 is None or prim18.empty:
        return "—"
    series = prim18.dropna().sort_index()
    window = series[(series.index >= event.ts) & (series.index <= event.ts + timedelta(hours=48))]
    prev = None
    for ts, value in window.items():
        if prev is not None and prev - value >= 5.0:
            return f"{(ts - event.ts).total_seconds() / 3600:.0f} h"
        prev = value
    return "no response in 48 h"


def _rate_after(event: WettingEvent, prim12: pd.Series) -> str:
    """Dry-down slope over the three days after the event."""
    import numpy as np

    series = prim12.dropna().sort_index()
    window = series[(series.index > event.ts) & (series.index <= event.ts + timedelta(hours=72))]
    if len(window) < 6:
        return "—"
    hours = (window.index - window.index[0]).total_seconds() / 3600.0
    slope = float(np.polyfit(hours, window.to_numpy(dtype=float), 1)[0]) * 24.0
    return f"{slope:.1f} cb/day"


def _events_table(events, prim12, prim18, tz) -> str:
    if not events:
        return "<p>No wetting events detected.</p>"
    rows = []
    for event in events:
        rows.append(
            "<tr>"
            "<td>"
            + event.ts.tz_convert(tz)
            .strftime("%b %-d, %-I:%M %p")
            .replace("AM", "am")
            .replace("PM", "pm")
            + "</td>"
            f"<td>{event.label_text()} <span class='why'>({html_lib.escape(event.reason)})"
            "</span></td>"
            f"<td>{event.drop_cb:.0f} cb ({event.prev_value:.0f} → {event.value:.0f})</td>"
            f"<td>{_hours_to_18_response(event, prim18)}</td>"
            f"<td>{_rate_after(event, prim12)}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>When</th><th>Call</th><th>12\" drop</th>"
        "<th>18\" responded after</th><th>Dry-down next 3 days</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )


def _hindsight_table(field_cfg, events, prim12, tz, trigger) -> str:
    """Each morning: what the arithmetic would have said vs what happened.

    Works from the precomputed primary series and event list — slicing them
    "as of" each morning — rather than re-running the whole field analysis per
    morning, which would be quadratic in the season length.
    """
    series = prim12.dropna().sort_index()
    if series.empty:
        return "<p>No data for the hindsight table.</p>"
    first, last = series.index.min(), series.index.max()
    crossings = []
    below = True
    for ts, value in series.items():
        if below and value >= trigger:
            crossings.append(ts)
            below = False
        elif value < trigger - 2:  # small hysteresis so noise isn't a "crossing"
            below = True
    real_wettings = [e.ts for e in events if e.label != "artifact"]

    rows = []
    morning = (first.tz_convert(tz) + timedelta(days=3)).replace(
        hour=6, minute=0, second=0, microsecond=0
    )
    while morning.tz_convert("UTC") <= last:
        morning_utc = morning.tz_convert("UTC")
        visible = series[series.index <= morning_utc]
        current = float(visible.iloc[-1]) if not visible.empty else None
        last_wet = max((t for t in real_wettings if t <= morning_utc), default=None)
        dry = dry_down_rate(visible, last_wet, morning_utc)
        outlook = trigger_outlook(visible, current, dry, trigger, morning_utc, tz)
        if outlook.status == "at_or_past":
            said = "already at/past"
        elif outlook.status == "eta" and outlook.days is not None:
            eta = morning_utc + timedelta(days=outlook.days)
            said = eta.tz_convert(tz).strftime("%b %-d")
        else:
            said = "—"
        actual = next((c for c in crossings if c >= morning_utc), None)
        actual_text = actual.tz_convert(tz).strftime("%b %-d") if actual is not None else "—"
        error = ""
        if outlook.status == "eta" and outlook.days is not None and actual is not None:
            delta = (morning_utc + timedelta(days=outlook.days) - actual).total_seconds() / 86400
            error = f"{delta:+.1f} d"
        rate = (
            f"{dry.slope_cb_per_day:.1f}" if dry.slope_cb_per_day is not None else "—"
        )
        rows.append(
            "<tr>"
            f"<td>{morning.strftime('%b %-d')}</td>"
            f"<td>{'—' if current is None else f'{current:.0f}'}</td>"
            f"<td>{rate}</td>"
            f"<td>{said}</td>"
            f"<td>{actual_text}</td>"
            f"<td>{error}</td>"
            "</tr>"
        )
        morning += timedelta(days=1)
    return (
        "<table><thead><tr><th>Morning</th><th>12\" cb</th><th>rate cb/day</th>"
        f"<th>would have said (reach {trigger:.0f})</th><th>actually crossed</th>"
        "<th>miss</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _field_readings(cfg: Config, field_cfg: FieldConfig, store: Store | None) -> pd.DataFrame:
    if store is not None:
        frame = store.readings_frame(field_cfg.key)
        if not frame.empty:
            return frame
    fixture = cfg.fixtures_dir / "synthetic" / f"{field_cfg.key}.csv"
    if fixture.exists():
        return load_irrocloud_csv(fixture, field_cfg.key, cfg.timezone_name).readings
    return pd.DataFrame()


def build_replay_report(
    cfg: Config,
    out_path: Path | None = None,
    precip_fn=weather_mod.fetch_historical_precip,
) -> Path:
    tz = cfg.tz
    store = Store(cfg.db_path) if cfg.db_path.exists() else None
    sections = []
    used_fixtures = False
    for field_cfg in cfg.ordered_fields():
        readings = _field_readings(cfg, field_cfg, store)
        if store is None or store.readings_frame(field_cfg.key).empty:
            used_fixtures = used_fixtures or not readings.empty
        if readings.empty:
            sections.append(
                f"<section><h2>{html_lib.escape(field_cfg.name)}</h2>"
                "<p>No readings available.</p></section>"
            )
            continue

        handline_ids = {
            f"{field_cfg.key}_{letter}_{depth}"
            for letter in field_cfg.handline_probes()
            for depth in (12, 18)
        }
        wide12 = pivot_depth(readings, 12.0, exclude_sensor_ids=handline_ids)
        wide18 = pivot_depth(readings, 18.0, exclude_sensor_ids=handline_ids)
        prim12 = primary_frame(wide12)
        prim18 = primary_frame(wide18)

        precip = None
        if precip_fn is not None and not wide12.empty:
            precip = precip_fn(
                field_cfg.lat,
                field_cfg.lon,
                wide12.index.min().tz_convert(tz).date(),
                wide12.index.max().tz_convert(tz).date(),
                cfg.timezone_name,
            )
        events = detect_wetting_events(
            prim12["primary"] if not prim12.empty else pd.Series(dtype=float),
            wide12,
            precip,
            tz,
        )

        chart = _svg_chart(
            field_cfg, wide12, wide18, prim12, events, tz, field_cfg.trigger_cb
        )
        trigger_note = (
            " <em>(placeholder trigger 50 cb)</em>" if field_cfg.trigger_is_placeholder else ""
        )
        rain_note = (
            "" if precip is not None
            else "<p class='why'>Rain records were unavailable when this report was "
            "built, so events are labeled from sensor shape alone (\"rain unknown\").</p>"
        )
        sections.append(
            f"<section><h2>{html_lib.escape(field_cfg.name)} "
            f"<span class='sub'>({field_cfg.crop}, {field_cfg.acres:.0f} ac)"
            f"{trigger_note}</span></h2>"
            + _legend()
            + chart
            + rain_note
            + "<h3>Wetting events</h3>"
            + _events_table(
                events,
                prim12["primary"] if not prim12.empty else pd.Series(dtype=float),
                prim18["primary"] if not prim18.empty else pd.Series(dtype=float),
                tz,
            )
            + "<h3>Days-to-trigger, morning by morning (hindsight check)</h3>"
            + _hindsight_table(
                field_cfg,
                events,
                prim12["primary"] if not prim12.empty else pd.Series(dtype=float),
                tz,
                field_cfg.trigger_cb,
            )
            + "</section>"
        )
    if store is not None:
        store.close()

    built = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    source_note = (
        "Built from the BUILT-IN SYNTHETIC sample season (no real field data yet)."
        if used_fixtures
        else "Built from the stored IrroCloud readings."
    )
    html = f"""<meta charset="utf-8">
<title>Season replay — soil tension</title>
<style>
  body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
         background: {PAGE}; color: {INK}; max-width: 940px;
         margin: 24px auto; padding: 0 16px; }}
  h1 {{ font-size: 22px; }} h2 {{ font-size: 18px; margin: 28px 0 4px; }}
  h3 {{ font-size: 14px; margin: 16px 0 6px; color: {INK_2}; }}
  .sub {{ font-weight: normal; color: {INK_2}; font-size: 14px; }}
  .why {{ color: {MUTED}; font-size: 12px; }}
  table {{ border-collapse: collapse; font-size: 12.5px; margin: 4px 0 12px; }}
  th, td {{ text-align: left; padding: 3px 12px 3px 0; border-bottom: 1px solid {GRID};
            font-variant-numeric: tabular-nums; }}
  th {{ color: {MUTED}; font-weight: 600; }}
  section {{ break-inside: avoid-page; }}
  @media print {{ body {{ background: white; }} }}
</style>
<h1>Season replay — soil tension by field</h1>
<p class="sub">{source_note} Generated {built}. Tension in centibars
(higher = drier); times in {cfg.timezone_name}. The bold line is the primary
series Helios uses: the driest probe after outlier screening.</p>
{"".join(sections)}
"""
    out_path = out_path or (cfg.reports_dir / "season-replay.html")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
