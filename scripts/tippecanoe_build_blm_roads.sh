#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="$REPO_DIR/build/vector_tiles"
STAGING_FILE="$OUTPUT_DIR/blm_public_roads_staging.geojson"
MANIFEST_FILE="$OUTPUT_DIR/blm_public_roads_staging_manifest.json"
MBTILES="$OUTPUT_DIR/blm_public_roads.mbtiles"

if [ ! -f "$STAGING_FILE" ] || [ ! -f "$MANIFEST_FILE" ]; then
  echo "ERROR: BLM staging output is missing."
  echo "Run: python3 scripts/build_blm_public_roads.py"
  exit 1
fi
if ! command -v tippecanoe &>/dev/null; then
  echo "ERROR: tippecanoe is not installed."
  exit 1
fi

read_manifest_value() {
  local key="$1"
  python3 -c "import json; print(json.load(open('$MANIFEST_FILE')).get('$key', ''))"
}

LAYER_NAME=$(read_manifest_value "layer_name")
MIN_ZOOM=$(read_manifest_value "min_zoom")
MAX_ZOOM=$(read_manifest_value "max_zoom")
BASE_ZOOM=$(read_manifest_value "base_zoom")

tippecanoe \
  --force \
  --no-tile-compression \
  --layer="$LAYER_NAME" \
  --minimum-zoom="$MIN_ZOOM" \
  --maximum-zoom="$MAX_ZOOM" \
  --base-zoom="$BASE_ZOOM" \
  -o "$MBTILES" \
  "$STAGING_FILE"

test -s "$MBTILES"
du -h "$MBTILES"
