# Provenance of these schema models

**Status: RECONSTRUCTED — not copied.**

The build plan was to copy `helios/schemas/inputs.py` and `helios/schemas/outputs.py`
verbatim from a read-only snapshot of the HELIOS repository. The snapshot download was
blocked by this sandbox's network policy on 2026-08-24 (the proxy returned HTTP 403 for
the GitHub tarball request), so the models in this package were instead **reconstructed
by hand from Appendix A of the build brief**, which was extracted from the same snapshot
on 2026-08-23.

Consequences:

- Request models (`inputs.py`) use `extra="forbid"` so tests catch typos in payloads we
  author, but field names, types, and constraints must be verified against the real
  `helios/schemas/inputs.py` in the morning.
- Response models (`outputs.py`) use `extra="allow"` so a live response with fields
  Appendix A did not list will still validate.
- No file hashes are available (nothing was copied).

**Morning task (in NEXT.md):** download the snapshot, diff these models against the
real ones, replace them with verbatim copies (plus attribution comments), and record
the snapshot date and file hashes here.

These models are used by tests and by `bridge/helios.py` to validate every payload
before it is sent, and to build the offline stub response.
