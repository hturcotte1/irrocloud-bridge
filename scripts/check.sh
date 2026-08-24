#!/usr/bin/env bash
# Everything that must be green before pushing: lint + the whole test suite.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== ruff (style and mistakes) =="
python3 -m ruff check .

echo "== pytest (the whole suite; no network needed) =="
python3 -m pytest tests/

echo ""
echo "All checks green."
