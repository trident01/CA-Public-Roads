#!/usr/bin/env bash
# Start a local server to view the map (Range-request aware for PMTiles)
# Then open http://localhost:8080 in your browser
set -euo pipefail
cd "$(dirname "$0")"
exec python3 scripts/serve_range.py 8080
