"""IrroCloud → Helios bridge.

A small, self-contained, deliberately throwaway tool: every morning it downloads
soil-tension readings from IrroCloud for four fields, cleans them the way HELIOS
does, asks the live Helios service for its forecast, saves the run into the
grower's Helios history, and sends a short plain-English email.

This is a temporary bridge, not an integration; a proper ingest path inside
Helios replaces it (see docs/HANDOFF-FOR-MARCO.md).
"""

__version__ = "0.1.0"
