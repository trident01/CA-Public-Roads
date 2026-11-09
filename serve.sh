#!/usr/bin/env bash
# Start a local server to view the map (Range-request aware for PMTiles)
# Then open http://localhost:8080 in your browser
set -euo pipefail
cd "$(dirname "$0")"
if [ -z "${PYTHON_BIN:-}" ] && [ -x .venv-pmtiles/bin/python ]; then
  PYTHON_BIN=.venv-pmtiles/bin/python
fi
PYTHON_BIN="${PYTHON_BIN:-python3}"
exec "$PYTHON_BIN" scripts/serve_range.py "${PORT:-8080}"
