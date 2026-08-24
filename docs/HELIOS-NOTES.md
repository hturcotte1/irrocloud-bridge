# HELIOS reference notes

**Provenance:** the live snapshot download of the HELIOS repository was blocked by this
sandbox's network policy on 2026-08-24 (the proxy returned HTTP 403). Everything below is
taken from Appendix A of the build brief, which was extracted from the same snapshot on
2026-08-23. **Verify these facts against the real snapshot in the morning** (see NEXT.md).

## Parser rules (`helios/scripts/parse_irrocloud_data.py`)

- `SENSOR_COLUMNS = ["sensor_a_12", "sensor_a_18", "sensor_b_12", "sensor_b_18",
  "sensor_c_12", "sensor_c_18"]`, mapped **in order** from the export columns SM1..SM6.
- Two known export layouts:
  - **Modern:** first line starts with `Timestamp,` — read with pandas, require columns
    `Timestamp,SM1..SM6` (extra columns are allowed; select only what is needed).
  - **Legacy:** two junk lines to skip, no header; require at least 8 columns; take
    columns `[0, 2, 3, 4, 5, 6, 7]` (zero-indexed) as timestamp + six sensors.
- Cleaning, per sensor column: `to_numeric(errors="coerce")`; mask values `== 254`
  (sentinel for a bad/disconnected reading); mask values `< 0`; `clip(upper=240)`.
- Row rules: drop rows with no parseable timestamp; drop rows where all six sensors are
  null; sort by timestamp; `drop_duplicates("timestamp", keep="last")`.
- Timestamps parsed with `utc=True, errors="coerce"` in HELIOS. (The bridge instead
  treats naive export timestamps as `America/Boise` — see DECISIONS.md; the morning
  discovery run confirms the real export timezone.)
- `_sensor_depth_in(sensor_id) = float(sensor_id.rsplit("_", 1)[1])` — the depth is the
  last underscore-separated token of the sensor id. The bridge's
  `{field_key}_{probe}_{depth}` ids keep that convention.

## Constants (`helios/agronomy/constants.py`, `helios/data/ingestion.py`)

| Constant | Value |
|---|---|
| `MAX_TENSION_CB` | 240.0 |
| `RESET_THRESHOLD_CB` | 15.0 |
| `ARTIFACT_DROP_THRESHOLD_CB` | 100.0 |
| `ARTIFACT_CEILING_FLOOR_CB` | 225.0 |
| `MAX_GAP` | 3 hours |
| `PRECIPITATION_THRESHOLD_IN` | 0.0 |
| `SENSOR_OUTLIER_MAD_MULTIPLIER` | 3.0 |

`ALLOWED_HORIZONS` = {24, 48, 72}. `MAX_PHYSICAL_SENSOR_COUNT`: not captured in
Appendix A as a number — the bridge caps readings conservatively and keeps the newest;
confirm the real value from the snapshot in the morning.

## Primary-sensor selection (`helios/data/ingestion.py::_select_primary_sensor`)

At each timestamp, among sensors that have a value:

1. If three or more report, compute the median and the MAD (median absolute deviation).
2. If MAD > 0, keep only sensors within `3.0 × MAD` of the median.
3. Return the **highest tension** (driest) among the kept sensors; ties broken by
   sensor id.

"Driest zone leads" is the conservative choice; the bridge mirrors it exactly.

## Wetting-event detection (`detect_wetting_events`)

A drop greater than 15 cb in the primary series between consecutive rows, **within a
continuous run** (a gap over three hours breaks continuity). Labeling:

- **artifact** if the previous value was ≥ 225 (ceiling minus 15), or the drop exceeds
  100 cb, or every probe dropped more than 15 at once;
- otherwise **ambiguous** if any precipitation fell that day;
- otherwise **irrigation** (low confidence).

## Web API (`helios/api/routes.py`, `account_auth.py`, `config.py`)

- `POST /web/auth/login`, JSON body `{"email": str, "password": str}` →
  `WebSessionResponse {email, district_id, display_name, onboarding_required,
  required_field_count, fields: [FarmerFieldResponse]}`. Sets cookie `helios_session`,
  which must be carried on every later call. 401 on bad credentials; 503 if web auth is
  not configured.
- `GET /web/auth/me` and `GET /web/fields` — same response type as login (so the field
  list from login is authoritative; `/web/fields` is a confirmation).
- `POST /web/predict` — body `PredictionRequestPayload`, response `PredictionResponse`.
- `GET /web/runs` → `{run_history: [...], saved_runs: [...]}`.
- `POST /web/runs` — body `{"run": {...}, "saved": bool}`; `run.id` is a required
  non-empty string.
- Also `/web/api/acknowledgements` and `/web/api/irrigation-passes` (not needed).
- Rate limit: 60 requests per 60-second window by default. The bridge spaces calls at
  least two seconds apart.
- Weather enrichment: when `weather` is omitted the server fetches NOAA
  (`api.weather.gov`) and raises an enrichment error if that fails. The bridge omits
  `weather` normally and supplies an Open-Meteo `caller_supplied` patch only as the
  last-resort retry.

## Request schema (`helios/schemas/inputs.py`)

See `bridge/_helios_schemas/inputs.py` (reconstructed from Appendix A — the snapshot
could not be downloaded tonight). Key points:

- `forecast_horizon_hours` must be 24, 48, or 72.
- `soil_moisture_readings` requires at least one reading; all readings in one request
  share one `field_id` and one measurement type.
- `SoilMoistureReading`: `timestamp`, `field_id`, `sensor_id`,
  `measurement_type="soil_water_tension"`, `unit="centibar"`, `value`,
  `volumetric_water_content` (null for tension), `depth_in` (> 0), `source`,
  `quality_flag`.
- `crop.canopy_status` is the literal `"active"`.
- Saved field setup (what `/web/fields` returns per field, lowercased `field_key`):
  `field_key, field_name, field_area_acres, crop_type, growth_stage (default flowering),
  soil_texture (loam), drainage_class (moderate), infiltration_rate (0.47),
  slope_pct (1.0), location_lat, location_lon, irrigation_type, pump_capacity (0.24),
  budget_dollars (0), budget_constraint_enabled (false),
  budget_constraint_review_required (false)`. The bridge maps these into the request
  blocks the way the browser does.

## Response schema (`helios/schemas/outputs.py`)

See `bridge/_helios_schemas/outputs.py`. Key points for the bridge:

- `predicted_moisture.moisture_24h` is in centibars when `measurement_type` is
  `soil_water_tension`.
- `persistence_baseline.method` is `"driest_zone_carry_forward"`.
- `explanation.operator_review_required` and `explanation.review_gate_reason` **must be
  surfaced, never stripped**. The tension model (XGBoost, trained 2026-08-01) is
  quarantined by a pre-registered accuracy gate; only the 24-hour horizon is served by
  the model, 48/72 fall back to persistence. Held-out error at 24 h: 5.47 cb model vs
  5.60 cb persistence. On Whitted the model is worse than persistence at 48 and 72 h.
- Quarantine sentence used as `review_gate_reason`:
  > "Tension model is quarantined after a failed pilot accuracy evaluation; operator
  > review is required before irrigation."
- `physics_baseline` is VWC-only; the bridge ignores it.

## Run object (`src/api/run-builders.js::mapApiRun`)

Begins with `id` (string), `title` ("<field name> • <page title>"), `timestamp` (ISO),
`prompt`, `decision`, `measurementType`, `recommendedAmountIn`, `uncappedNeedIn`,
`caps`, `bindingConstraint`, `finalAmountIn`, `dripRuntime`, `timingWindow`, and
continues with more fields derived from the response and the field inputs. **The full
function was not available tonight.** The bridge therefore templates the shape at
runtime: before saving, it calls `GET /web/runs` and uses the newest `run_history`
entry as the shape template; if the shape cannot be confirmed, it skips the save and
logs why instead of writing garbage into Jacob's history. Morning task: read
`mapApiRun` from the snapshot and confirm/extend `bridge/helios.py::build_run_object`.

## Field metadata (`data/pilot/irrocloud/field_metadata.json`)

| Field | key | Lat | Lon | Acres | Crop | Planted |
|---|---|---|---|---|---|---|
| Cunningham 6 | `cunningham-6` | 43.008861 | −116.145528 | 100 under pivot (225 total) | corn | 2026-05-02 |
| RV80 | `rv80` | 43.036389 | −116.193333 | 73 | sugarbeet | 2026-04-09 |
| Bennett 1 N | `bennett-1-n` | 43.04075 | −116.102917 | 98 | sugarbeet | 2026-03-30 |
| Whitted | `whitted` | 43.037056 | −116.132 | 140 | corn | 2026-04-15 |

- All: soil loam, irrigation pivot, probes a/b/c at 12 and 18 inches.
- Bennett 1 N: "HLS probe is in the handlines, not the pivot portion of the field.
  Data from either probe is usable; pivot probe may be better." Which letter is the
  handline probe is unknown tonight (morning question).
- Whitted: "SS = solid set handlines, left out all season, not moved. Main pivot probe
  likely better than the handline probe." Same morning question.
- Cadence 2026: unbroken hourly runs May → July 8 (1,465–1,631 observations per field).
  Helios's stored data ends July 8, 2026, so the first real fetch back-fills from
  July 1.

## Review gate (`helios/services/recommendation_service.py`, search `QUARANTINED`)

Every prediction response carries `operator_review_required` with a
`review_gate_reason`. This is by design and it is correct. The bridge never hides,
strips, or paraphrases it away; Jacob's email footer states the model is under review
and the forecast is not something to irrigate on by itself.
