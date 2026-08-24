"""Per-field analysis: primary series, wetting resets, dry-down rate, trigger.

Every rule here mirrors HELIOS (see docs/HELIOS-NOTES.md) so the bridge and the
product agree about the data:

- primary sensor = driest inlier after a MAD outlier filter (×3.0, applied only
  when three or more sensors report at that timestamp);
- a wetting reset is a drop > 15 cb between consecutive readings inside a
  continuous run (a gap over three hours breaks continuity), with HELIOS's
  three artifact rules;
- the dry-down slope and days-to-trigger lines are plain arithmetic on the
  grower's own readings — deliberately NOT model output, and the email says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from bridge.config import FieldConfig

RESET_THRESHOLD_CB = 15.0
ARTIFACT_DROP_THRESHOLD_CB = 100.0
ARTIFACT_CEILING_FLOOR_CB = 225.0  # ceiling (240) minus reset threshold (15)
MAX_GAP = timedelta(hours=3)
MAD_MULTIPLIER = 3.0

STALE_AFTER = timedelta(hours=26)
FLAT_SLOPE_CB_PER_DAY = 0.5
FRESH_RESET_WINDOW = timedelta(hours=24)
SLOPE_WINDOW = timedelta(hours=72)
SLOPE_MIN_SPAN = timedelta(hours=12)
SLOPE_MIN_POINTS = 6


def pivot_depth(
    readings: pd.DataFrame, depth: float, exclude_sensor_ids: set[str] | None = None
) -> pd.DataFrame:
    """Tidy readings → wide frame (index ts_utc, one column per sensor) for one
    depth. ``exclude_sensor_ids`` removes handline probes from the headline."""
    subset = readings[readings["depth_in"] == depth]
    if exclude_sensor_ids:
        subset = subset[~subset["sensor_id"].isin(exclude_sensor_ids)]
    if subset.empty:
        return pd.DataFrame()
    return subset.pivot_table(
        index="ts_utc", columns="sensor_id", values="tension_cb", aggfunc="last"
    ).sort_index()


def _select_primary_row(values: pd.Series) -> tuple[float | None, float | None, str | None]:
    """(primary value, mean of the other inliers, primary sensor id)."""
    vals = values.dropna()
    if vals.empty:
        return None, None, None
    if len(vals) >= 3:
        median = vals.median()
        mad = (vals - median).abs().median()
        if mad > 0:
            vals = vals[(vals - median).abs() <= MAD_MULTIPLIER * mad]
    max_val = float(vals.max())
    winner = sorted(vals[vals == max_val].index)[0]  # ties → sensor id order
    others = vals.drop(winner)
    others_mean = float(others.mean()) if len(others) else None
    return max_val, others_mean, str(winner)


def primary_frame(wide: pd.DataFrame) -> pd.DataFrame:
    """Columns: primary, others_mean, primary_sensor — one row per timestamp."""
    if wide.empty:
        return pd.DataFrame(columns=["primary", "others_mean", "primary_sensor"])
    rows = [_select_primary_row(wide.loc[ts]) for ts in wide.index]
    return pd.DataFrame(
        rows, index=wide.index, columns=["primary", "others_mean", "primary_sensor"]
    )


@dataclass
class WettingEvent:
    ts: pd.Timestamp  # timestamp of the after-drop (wet) reading
    prev_ts: pd.Timestamp
    prev_value: float
    value: float
    drop_cb: float
    label: str  # "irrigation" | "ambiguous" | "artifact"
    reason: str
    rain_known: bool  # False when no precipitation data was available

    def label_text(self) -> str:
        if self.label == "irrigation" and not self.rain_known:
            return "irrigation (rain unknown)"
        return self.label


def detect_wetting_events(
    primary: pd.Series,
    wide: pd.DataFrame,
    precip_by_day: dict[date, float] | None = None,
    tz: ZoneInfo | None = None,
) -> list[WettingEvent]:
    """HELIOS wetting detection on a primary series.

    ``wide`` (all probes at this depth) feeds the "every probe dropped at once"
    artifact rule. ``precip_by_day`` maps field-local dates to inches; None
    means rain data was unavailable, which downgrades "irrigation" confidence
    but never blocks detection.
    """
    tz = tz or ZoneInfo("UTC")
    series = primary.dropna().sort_index()
    events: list[WettingEvent] = []
    for prev_ts, ts in zip(series.index[:-1], series.index[1:], strict=False):
        if ts - prev_ts > MAX_GAP:
            continue  # a gap breaks continuity — no event across it
        prev_val, val = float(series[prev_ts]), float(series[ts])
        drop = prev_val - val
        if drop <= RESET_THRESHOLD_CB:
            continue

        all_dropped = False
        if not wide.empty and prev_ts in wide.index and ts in wide.index:
            pair = wide.loc[[prev_ts, ts]]
            both = pair.notna().all()
            diffs = pair.iloc[0][both] - pair.iloc[1][both]
            all_dropped = len(diffs) >= 2 and bool((diffs > RESET_THRESHOLD_CB).all())

        if prev_val >= ARTIFACT_CEILING_FLOOR_CB:
            label, reason, rain_known = "artifact", "previous reading was at the ceiling", True
        elif drop > ARTIFACT_DROP_THRESHOLD_CB:
            reason = f"drop of {drop:.0f} cb is implausibly large"
            label, rain_known = "artifact", True
        elif all_dropped:
            label, reason, rain_known = "artifact", "every probe dropped at once", True
        elif precip_by_day is None:
            label, reason, rain_known = "irrigation", "rain data unavailable", False
        elif precip_by_day.get(ts.tz_convert(tz).date(), 0.0) > 0.0:
            label, reason, rain_known = "ambiguous", "precipitation fell that day", True
        else:
            label, reason, rain_known = "irrigation", "no rain that day (low confidence)", True

        events.append(
            WettingEvent(
                ts=ts,
                prev_ts=prev_ts,
                prev_value=prev_val,
                value=val,
                drop_cb=drop,
                label=label,
                reason=reason,
                rain_known=rain_known,
            )
        )
    return events


def last_wetting(events: list[WettingEvent]) -> WettingEvent | None:
    """The most recent NON-artifact wetting (artifacts are glitches, not water)."""
    real = [e for e in events if e.label != "artifact"]
    return real[-1] if real else None


@dataclass
class DryDown:
    status: str  # "ok" | "flat" | "fresh_reset" | "insufficient"
    slope_cb_per_day: float | None = None
    window_start: pd.Timestamp | None = None
    n_points: int = 0


def dry_down_rate(
    primary: pd.Series,
    last_wet_at: pd.Timestamp | None,
    now_utc: datetime,
) -> DryDown:
    """Least-squares slope (cb/day) since the last wetting, capped to 72 h.

    Order of precedence (build brief §9): a reset under 24 h old wins ("new
    dry-down starting, no rate yet"), then the 12-hour / 6-reading minimums,
    then flat (≤ 0.5 cb/day)."""
    now = pd.Timestamp(now_utc)
    if last_wet_at is not None and now - last_wet_at < FRESH_RESET_WINDOW:
        return DryDown(status="fresh_reset")

    start = now - SLOPE_WINDOW
    if last_wet_at is not None and last_wet_at > start:
        start = last_wet_at
    window = primary.dropna()
    window = window[(window.index >= start) & (window.index <= now)]
    if len(window) < SLOPE_MIN_POINTS or (window.index.max() - window.index.min()) < SLOPE_MIN_SPAN:
        return DryDown(status="insufficient", window_start=start, n_points=len(window))

    hours = (window.index - window.index[0]).total_seconds() / 3600.0
    slope_per_day = float(np.polyfit(hours, window.to_numpy(dtype=float), 1)[0]) * 24.0
    status = "flat" if slope_per_day <= FLAT_SLOPE_CB_PER_DAY else "ok"
    return DryDown(
        status=status,
        slope_cb_per_day=slope_per_day,
        window_start=window.index.min(),
        n_points=len(window),
    )


def _part_of_day(hour: int) -> str:
    if hour < 5:
        return "overnight"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


@dataclass
class TriggerOutlook:
    status: str  # "eta" | "at_or_past" | "none"
    days: float | None = None
    eta_text: str | None = None  # "Thursday afternoon (Aug 27)"
    crossed_since: pd.Timestamp | None = None


def trigger_outlook(
    primary: pd.Series,
    current: float | None,
    dry: DryDown,
    trigger_cb: float,
    now_utc: datetime,
    tz: ZoneInfo,
) -> TriggerOutlook:
    """Days-to-trigger arithmetic: (trigger − current) / slope, only when the
    slope is a real rate (> 0.5 cb/day) and the field is below the trigger."""
    if current is None:
        return TriggerOutlook(status="none")

    if current >= trigger_cb:
        # Walk back through consecutive readings ≥ trigger to find when the
        # current excursion started.
        series = primary.dropna().sort_index()
        crossed = series.index[-1]
        for ts in reversed(series.index):
            if series[ts] >= trigger_cb:
                crossed = ts
            else:
                break
        return TriggerOutlook(status="at_or_past", crossed_since=crossed)

    slope = dry.slope_cb_per_day
    if dry.status != "ok" or not slope or slope <= FLAT_SLOPE_CB_PER_DAY:
        return TriggerOutlook(status="none")

    days = (trigger_cb - current) / slope
    eta_local = (pd.Timestamp(now_utc) + timedelta(days=days)).tz_convert(tz)
    if days > 7:
        eta_text = "more than a week out at this rate"
    else:
        eta_text = (
            f"{eta_local.strftime('%A')} {_part_of_day(eta_local.hour)}"
            f" ({eta_local.strftime('%b %-d')})"
        )
    return TriggerOutlook(status="eta", days=days, eta_text=eta_text)


@dataclass
class DepthSummary:
    depth_in: float
    primary: float | None = None
    others_mean: float | None = None
    primary_sensor: str | None = None


@dataclass
class FieldSnapshot:
    """Everything the message composer needs for one field."""

    field_key: str
    as_of_utc: pd.Timestamp | None
    stale: bool
    d12: DepthSummary
    d18: DepthSummary
    dry: DryDown
    last_wet: WettingEvent | None
    outlook12: TriggerOutlook
    outlook18: TriggerOutlook
    mention_18: bool
    handline_latest: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    primary12_series: pd.Series | None = None  # reused for scoring + persistence


def analyze_field(
    field_cfg: FieldConfig,
    readings: pd.DataFrame,
    now_utc: datetime,
    tz: ZoneInfo,
    precip_by_day: dict[date, float] | None = None,
) -> FieldSnapshot:
    """The per-field snapshot (build brief §9). ``readings`` is the tidy frame
    from the store; nothing here may break on a gap or an empty probe."""
    handline_ids = {
        f"{field_cfg.key}_{letter}_{int(depth)}"
        for letter in field_cfg.handline_probes()
        for depth in (12, 18)
    }

    if readings.empty:
        empty = DepthSummary(12.0), DepthSummary(18.0)
        return FieldSnapshot(
            field_key=field_cfg.key,
            as_of_utc=None,
            stale=True,
            d12=empty[0],
            d18=empty[1],
            dry=DryDown(status="insufficient"),
            last_wet=None,
            outlook12=TriggerOutlook(status="none"),
            outlook18=TriggerOutlook(status="none"),
            mention_18=False,
            notes=["no readings at all for this field"],
        )

    wide12 = pivot_depth(readings, 12.0, exclude_sensor_ids=handline_ids)
    wide18 = pivot_depth(readings, 18.0, exclude_sensor_ids=handline_ids)
    prim12 = primary_frame(wide12)
    prim18 = primary_frame(wide18)
    primary12 = prim12["primary"] if not prim12.empty else pd.Series(dtype=float)
    primary18 = prim18["primary"] if not prim18.empty else pd.Series(dtype=float)

    as_of = readings["ts_utc"].max()
    stale = (pd.Timestamp(now_utc) - as_of) > STALE_AFTER

    def latest(depth_frame: pd.DataFrame, depth: float) -> DepthSummary:
        if depth_frame.empty or depth_frame["primary"].dropna().empty:
            return DepthSummary(depth)
        last_ts = depth_frame["primary"].dropna().index[-1]
        row = depth_frame.loc[last_ts]
        others = row["others_mean"]
        return DepthSummary(
            depth_in=depth,
            primary=float(row["primary"]),
            others_mean=None if pd.isna(others) else float(others),
            primary_sensor=row["primary_sensor"],
        )

    d12 = latest(prim12, 12.0)
    d18 = latest(prim18, 18.0)

    events = detect_wetting_events(primary12, wide12, precip_by_day, tz)
    last_wet = last_wetting(events)
    dry = dry_down_rate(primary12, last_wet.ts if last_wet else None, now_utc)

    outlook12 = trigger_outlook(
        primary12, d12.primary, dry, field_cfg.trigger_cb, now_utc, tz
    )
    # 18" gets its own slope so its outlook is honest, not borrowed from 12".
    events18 = detect_wetting_events(primary18, wide18, precip_by_day, tz)
    last_wet18 = last_wetting(events18)
    dry18 = dry_down_rate(primary18, last_wet18.ts if last_wet18 else None, now_utc)
    outlook18 = trigger_outlook(
        primary18, d18.primary, dry18, field_cfg.trigger_cb, now_utc, tz
    )

    # Mention 18" only when it tells a DIFFERENT story than 12" (§9). The deep
    # sensor lagging the shallow one is the normal story and stays quiet; what
    # earns a mention is 18" being at/past the trigger when 12" is not, or 18"
    # arriving at the trigger a full day EARLIER than 12".
    mention_18 = False
    if outlook18.status == "at_or_past" and outlook12.status != "at_or_past":
        mention_18 = True
    elif (
        outlook12.status == "eta"
        and outlook18.status == "eta"
        and outlook12.days is not None
        and outlook18.days is not None
        and outlook18.days <= outlook12.days - 1.0
    ):
        mention_18 = True

    handline_latest: dict[str, float] = {}
    for sensor_id in sorted(handline_ids):
        sensor_rows = readings[readings["sensor_id"] == sensor_id].dropna(subset=["tension_cb"])
        if not sensor_rows.empty:
            handline_latest[sensor_id] = float(sensor_rows.iloc[-1]["tension_cb"])

    notes: list[str] = []
    if d12.primary is None:
        notes.append('no usable 12" readings')
    if d18.primary is None:
        notes.append('no usable 18" readings')

    return FieldSnapshot(
        field_key=field_cfg.key,
        as_of_utc=as_of,
        stale=stale,
        d12=d12,
        d18=d18,
        dry=dry,
        last_wet=last_wet,
        outlook12=outlook12,
        outlook18=outlook18,
        mention_18=mention_18,
        handline_latest=handline_latest,
        notes=notes,
        primary12_series=primary12,
    )
