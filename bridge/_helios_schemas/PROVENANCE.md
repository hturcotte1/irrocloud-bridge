# Provenance of these schema models

**Status: RECONSTRUCTED, then VERIFIED against the live service (2026-08-24).**

The build plan was to copy `helios/schemas/inputs.py` and `helios/schemas/outputs.py`
verbatim from a read-only snapshot of the HELIOS repository. The snapshot download was
blocked by the sandbox's network policy on the night of 2026-08-24, so the models were
**reconstructed by hand from Appendix A of the build brief** (extracted from the same
snapshot on 2026-08-23).

On the morning of 2026-08-24 the snapshot was still unreachable (no accessible GitHub
repository holds the HELIOS app source — the accessible `HeliosNeuralNetwork` repo is a
concept README, `HeliosV0.03` is empty). Instead, every API-shape claim was **verified
field-by-field against the deployed service's own contract**: `GET /openapi.json` from
the live Helios at `https://irrigant-helios.up.railway.app` (Helios 0.5.0, OpenAPI 3.1,
54,907 bytes, sha256 `8af314d14a05b723…`). That document is committed verbatim as
`docs/helios-openapi-2026-08-24.json` — for these purposes it is *better* than a repo
snapshot, because it is what the running server actually enforces.

The verification was done by independent comparison passes with every claimed
divergence re-checked adversarially; 12 candidate mismatches were refuted as
harmless (bridge-stricter-than-server request fields), and these were real and fixed:

- **`future_irrigation` may never be JSON null** — the server field is a non-nullable
  object, so the reconstruction's `None` default meant every live `/web/predict` would
  have been rejected with HTTP 422. Now a `FutureIrrigationInput` model
  (`state="missing_evidence"`) is always sent.
- **`Operational.budget_constraint_enabled` server default is `true`**, not `false`;
  default and fallback now mirror it (Jacob's saved setups pass through explicitly).
- **`WeatherInputPatch` scalars are server-bounded** (temp −40…130 °F, humidity
  0…100 %, wind 0…80 mph, precip 0…12 in, solar 0…35 MJ/m²); the bounds are mirrored
  and `weather_patch_from_snapshot` clamps live Open-Meteo values into them.
- **`applied_in` ≥ 0** on irrigation events; **lat/lon bounds** on the payload.
- **`PredictionResponse.reference_et` is an object** (`{value, unit}`), not a bare
  number — a live response would have failed validation.
- **Helios is dual-mode and its response schemas default to VWC-fraction**, not
  tension. Response defaults now mirror that, and `_result_from_response` refuses to
  read a non-tension answer as centibars.
- **`uncapped_need_in`** is non-nullable with default 0.
- **`WebSessionResponse.fields` is optional** — the client now falls back to
  `GET /web/fields` and words the "no fields at all" alert accurately.

Still deliberate bridge policy (stricter than the wire, kept on purpose):
`forecast_horizon_hours` as `Literal[24, 48, 72]` (the wire type is a plain integer),
tension-only literals on `SoilMoistureReading`, required-ness of fields the bridge
always supplies.

**Not verifiable without the HELIOS source** (the OpenAPI covers API shapes only):
the parser rules and constants in `docs/HELIOS-NOTES.md`, `MAX_PHYSICAL_SENSOR_COUNT`,
and `mapApiRun` in `src/api/run-builders.js`. Those remain Appendix-A provenance; the
run-save path still template-matches Jacob's own history before writing anything
(the server accepts the run as a free-form object — `WebRunUpsertRequest.run` is
`additionalProperties: true` — so the shape discipline is the frontend's, and the
bridge's caution stands).

These models are used by tests and by `bridge/helios.py` to validate every payload
before it is sent, and to build the offline stub response.
