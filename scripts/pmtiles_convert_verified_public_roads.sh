#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="$REPO_DIR/build/vector_tiles"
PUBLISH_DIR="$REPO_DIR/vector_tiles"
MBTILES="$OUTPUT_DIR/verified_public_roads.mbtiles"
PMTILES="$OUTPUT_DIR/verified_public_roads.pmtiles"
PUBLISHED_PMTILES="$PUBLISH_DIR/verified_public_roads.pmtiles"
MANIFEST="$OUTPUT_DIR/verified_public_roads_staging_manifest.json"
PUBLISHED_MANIFEST="$PUBLISH_DIR/verified_public_roads_staging_manifest.json"

if [ ! -s "$MBTILES" ]; then
  echo "ERROR: verified-public-road MBTiles are missing."
  echo "Run: bash scripts/tippecanoe_build_verified_public_roads.sh"
  exit 1
fi
if ! command -v pmtiles &>/dev/null; then
  echo "ERROR: pmtiles CLI is not installed."
  exit 1
fi

mkdir -p "$PUBLISH_DIR"
pmtiles convert "$MBTILES" "$PMTILES"
cp "$PMTILES" "$PUBLISHED_PMTILES"
cp "$MANIFEST" "$PUBLISHED_MANIFEST"
test -s "$PUBLISHED_PMTILES"
du -h "$PUBLISHED_PMTILES"
