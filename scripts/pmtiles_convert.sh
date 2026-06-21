#!/usr/bin/env bash
# Convert the MBTiles file into PMTiles for direct browser serving.
# The vector_preview.html page loads PMTiles via the pmtiles:// protocol.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="$REPO_DIR/build/vector_tiles"
MBTILES="$OUTPUT_DIR/forest_roads.mbtiles"
PMTILES="$OUTPUT_DIR/forest_roads.pmtiles"
PMTILES_CMD=""
PMTILES_RUNNER=()

pmtiles_command_works() {
  "$@" --help >/dev/null 2>&1
}

echo "==> pmtiles_convert.sh"
echo "    Input:  $MBTILES"
echo "    Output: $PMTILES"

# ── pre-flight checks ───────────────────────────────────────────────────
if [ ! -f "$MBTILES" ]; then
  echo "ERROR: MBTiles not found: $MBTILES"
  echo "Run: bash scripts/tippecanoe_build.sh"
  exit 1
fi

if command -v pmtiles &>/dev/null && pmtiles_command_works pmtiles; then
  PMTILES_CMD="pmtiles"
  PMTILES_RUNNER=("pmtiles")
elif command -v pmtiles-convert &>/dev/null && pmtiles_command_works pmtiles-convert; then
  PMTILES_CMD="pmtiles-convert"
  PMTILES_RUNNER=("pmtiles-convert")
elif [ -x "$REPO_DIR/.venv-pmtiles/bin/python" ] && [ -f "$REPO_DIR/.venv-pmtiles/bin/pmtiles-convert" ]; then
  PMTILES_CMD="python pmtiles-convert"
  PMTILES_RUNNER=("$REPO_DIR/.venv-pmtiles/bin/python" "$REPO_DIR/.venv-pmtiles/bin/pmtiles-convert")
else
  echo "ERROR: pmtiles CLI is not installed."
  echo "  python3 -m venv .venv-pmtiles"
  echo "  .venv-pmtiles/bin/python -m pip install pmtiles"
  exit 1
fi

# ── convert ─────────────────────────────────────────────────────────────
MB_SIZE=$(du -sh "$MBTILES" 2>/dev/null | cut -f1 || echo "?")
echo "    MBTiles size: $MB_SIZE"
echo ""
echo "    Converting ..."
if [ "$PMTILES_CMD" = "pmtiles" ]; then
  "${PMTILES_RUNNER[@]}" convert "$MBTILES" "$PMTILES"
else
  "${PMTILES_RUNNER[@]}" "$MBTILES" "$PMTILES" --overwrite
fi

# ── verify ──────────────────────────────────────────────────────────────
if [ ! -f "$PMTILES" ]; then
  echo "ERROR: conversion completed but $PMTILES was not created."
  exit 1
fi

PM_SIZE=$(du -sh "$PMTILES" 2>/dev/null | cut -f1 || echo "?")
echo "    Done -> $PMTILES ($PM_SIZE)"
