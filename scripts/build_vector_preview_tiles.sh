#!/usr/bin/env bash
# Build everything needed for vector_preview.html in one go:
# 1. Stage source GeoJSON
# 2. Generate MBTiles with Tippecanoe
# 3. Convert MBTiles -> PMTiles
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="$REPO_DIR/build/vector_tiles"
PUBLISH_DIR="$REPO_DIR/vector_tiles"
PMTILES_CMD=""
PMTILES_VERSION_CMD=()

pmtiles_command_works() {
  "$@" --help >/dev/null 2>&1
}

# ── pre-flight dependency check ─────────────────────────────────────────
echo "==> build_vector_preview_tiles.sh"
echo ""

if ! python3 -c "import json; print('ok')" &>/dev/null; then
  echo "ERROR: python3 is not available."
  exit 1
fi

if ! command -v tippecanoe &>/dev/null; then
  echo "ERROR: tippecanoe is not installed."
  echo "  macOS: brew install tippecanoe"
  echo "  Linux: sudo apt install tippecanoe"
  echo "  Docs:  https://github.com/felt/tippecanoe"
  echo ""
  echo "After installing, re-run this script."
  exit 1
fi

if command -v pmtiles &>/dev/null && pmtiles_command_works pmtiles; then
  PMTILES_CMD="pmtiles"
  PMTILES_VERSION_CMD=("pmtiles" "--help")
elif command -v pmtiles-convert &>/dev/null && pmtiles_command_works pmtiles-convert; then
  PMTILES_CMD="pmtiles-convert"
  PMTILES_VERSION_CMD=("pmtiles-convert" "--help")
elif [ -x "$REPO_DIR/.venv-pmtiles/bin/python" ] && [ -f "$REPO_DIR/.venv-pmtiles/bin/pmtiles-convert" ]; then
  PMTILES_CMD="python pmtiles-convert"
  PMTILES_VERSION_CMD=("$REPO_DIR/.venv-pmtiles/bin/python" "$REPO_DIR/.venv-pmtiles/bin/pmtiles-convert" "--help")
else
  echo "ERROR: pmtiles CLI is not installed."
  echo "  python3 -m venv .venv-pmtiles"
  echo "  .venv-pmtiles/bin/python -m pip install pmtiles"
  echo ""
  echo "After installing, re-run this script."
  exit 1
fi

echo "    python3    $(python3 --version 2>&1 | head -1)"
echo "    tippecanoe $(tippecanoe --version 2>&1 | head -1)"
echo "    $PMTILES_CMD    $("${PMTILES_VERSION_CMD[@]}" 2>&1 | head -1)"
echo ""

# ── step 1: stage ──────────────────────────────────────────────────────
echo "── Step 1: Stage source GeoJSON ──────────────────────────────────"
python3 "$SCRIPT_DIR/build_vector_tile_staging.py"
echo ""

# ── step 2: MBTiles ───────────────────────────────────────────────────
echo "── Step 2: Generate MBTiles with Tippecanoe ──────────────────────"
bash "$SCRIPT_DIR/tippecanoe_build.sh"
echo ""

# ── step 3: PMTiles ───────────────────────────────────────────────────
echo "── Step 3: Convert MBTiles -> PMTiles ────────────────────────────"
bash "$SCRIPT_DIR/pmtiles_convert.sh"
echo ""

# ── summary ────────────────────────────────────────────────────────────
echo "── Summary ────────────────────────────────────────────────────────"
echo "  Output directory:  $OUTPUT_DIR"
for f in "$OUTPUT_DIR"/*; do
  if [ -f "$f" ]; then
    SIZE=$(du -sh "$f" 2>/dev/null | cut -f1)
    printf "  %-50s %s\n" "$(basename "$f")" "$SIZE"
  fi
done
if [ -d "$PUBLISH_DIR" ]; then
  echo "  Published files:"
  for f in "$PUBLISH_DIR"/*; do
    if [ -f "$f" ]; then
      SIZE=$(du -sh "$f" 2>/dev/null | cut -f1)
      printf "  %-50s %s\n" "$(basename "$f")" "$SIZE"
    fi
  done
fi
echo ""
echo "  Preview at: http://127.0.0.1:8080/vector_preview.html"
