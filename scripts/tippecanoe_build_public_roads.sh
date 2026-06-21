#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="$REPO_DIR/build/vector_tiles"
STAGING_FILE="$OUTPUT_DIR/public_roads_staging.geojson"
MANIFEST_FILE="$OUTPUT_DIR/public_roads_staging_manifest.json"
MBTILES="$OUTPUT_DIR/public_roads.mbtiles"

echo "==> tippecanoe_build_public_roads.sh"
echo "    Staging: $STAGING_FILE"
echo "    Output:  $MBTILES"

if [ ! -f "$STAGING_FILE" ]; then
  echo "ERROR: staging file not found: $STAGING_FILE"
  echo "Run: python3 scripts/build_public_roads_vector_staging.py"
  exit 1
fi

if [ ! -f "$MANIFEST_FILE" ]; then
  echo "ERROR: manifest not found: $MANIFEST_FILE"
  echo "Run: python3 scripts/build_public_roads_vector_staging.py"
  exit 1
fi

if ! command -v tippecanoe &>/dev/null; then
  echo "ERROR: tippecanoe is not installed."
  echo "  macOS: brew install tippecanoe"
  echo "  Linux: sudo apt install tippecanoe"
  exit 1
fi

read_manifest_value() {
  local key="$1"
  python3 -c "import json; print(json.load(open('$MANIFEST_FILE')).get('$key', ''))" 2>/dev/null
}

STAGED_COUNT=$(read_manifest_value "staged_feature_count" || echo "?")
LAYER_NAME=$(read_manifest_value "layer_name")
MIN_ZOOM=$(read_manifest_value "min_zoom")
MAX_ZOOM=$(read_manifest_value "max_zoom")
BASE_ZOOM=$(read_manifest_value "base_zoom")

if [ -z "$LAYER_NAME" ] || [ -z "$MIN_ZOOM" ] || [ -z "$MAX_ZOOM" ] || [ -z "$BASE_ZOOM" ]; then
  echo "ERROR: manifest is missing one or more required Tippecanoe settings."
  exit 1
fi

echo "    Features: $STAGED_COUNT"
echo "    Layer:    $LAYER_NAME  Zoom: $MIN_ZOOM-$MAX_ZOOM  Base: $BASE_ZOOM"
echo ""
echo "    Running tippecanoe ..."
tippecanoe \
  --force \
  --no-tile-compression \
  --layer="$LAYER_NAME" \
  --minimum-zoom="$MIN_ZOOM" \
  --maximum-zoom="$MAX_ZOOM" \
  --base-zoom="$BASE_ZOOM" \
  --drop-densest-as-needed \
  --extend-zooms-if-still-dropping \
  -o "$MBTILES" \
  "$STAGING_FILE"

if [ ! -f "$MBTILES" ]; then
  echo "ERROR: tippecanoe completed but $MBTILES was not created."
  exit 1
fi

MB_SIZE=$(du -sh "$MBTILES" 2>/dev/null | cut -f1 || echo "?")
echo "    Done -> $MBTILES ($MB_SIZE)"
