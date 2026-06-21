#!/usr/bin/env python3
"""
check_vector_preview_outputs.py

Verify the vector-tile preview pipeline outputs exist and the manifest
contains the expected metadata keys.

This script reads nothing beyond the repo's build/vector_tiles/ directory.
It does NOT build tiles, run tippecanoe, or modify any files.
"""

import json
import os
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_DIR / "build" / "vector_tiles"

STAGING_GEOJSON = OUTPUT_DIR / "forest_roads_staging.geojson"
MANIFEST = OUTPUT_DIR / "forest_roads_staging_manifest.json"
MBTILES = OUTPUT_DIR / "forest_roads.mbtiles"
PMTILES = OUTPUT_DIR / "forest_roads.pmtiles"

REQUIRED_MANIFEST_KEYS = {
    "layer_name",
    "min_zoom",
    "max_zoom",
    "base_zoom",
    "tippecanoe_command",
    "staged_feature_count",
}


def check_file(path: Path, label: str) -> tuple[bool, str]:
    if not path.exists():
        return False, f"missing"
    size = path.stat().st_size
    if size == 0:
        return False, f"empty (0 bytes)"
    if size < 1024:
        return True, f"{size} B"
    if size < 1024 * 1024:
        return True, f"{size / 1024:.1f} KiB"
    return True, f"{size / (1024 * 1024):.1f} MiB"


def check_manifest() -> list[str]:
    errors: list[str] = []
    try:
        data = json.loads(MANIFEST.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        errors.append(f"  MANIFEST: parse error — {exc}")
        return errors

    for key in REQUIRED_MANIFEST_KEYS:
        if key not in data:
            errors.append(f"  MANIFEST: missing key \"{key}\"")

    # Validate types of numeric keys
    for key in ("min_zoom", "max_zoom", "base_zoom", "staged_feature_count"):
        val = data.get(key)
        if val is not None and not isinstance(val, (int, float)):
            errors.append(f"  MANIFEST: \"{key}\" should be numeric, got {type(val).__name__}")

    if not isinstance(data.get("layer_name"), str) or not data["layer_name"].strip():
        errors.append("  MANIFEST: \"layer_name\" must be a non-empty string")

    return errors


def print_summary(manifest_ok: bool) -> None:
    staging_ok, staging_size = check_file(STAGING_GEOJSON, "staging GeoJSON")
    manifest_exists, manifest_size = check_file(MANIFEST, "manifest")
    mbtiles_ok, mbtiles_size = check_file(MBTILES, "MBTiles")
    pmtiles_ok, pmtiles_size = check_file(PMTILES, "PMTiles")

    # Header
    print("=" * 56)
    print("  Vector-tile preview pipeline — output check")
    print("=" * 56)

    # Legend
    print("  Legend:  [OK]  [MISSING]  [EMPTY]  [?]")
    print()

    # Files table
    def status_tag(ok: bool, size: str) -> str:
        if "missing" in size:
            return "[MISSING]"
        if "empty" in size:
            return "[EMPTY] "
        return "[OK]     "

    files = [
        ("staging GeoJSON",        STAGING_GEOJSON.name, staging_ok, staging_size),
        ("manifest",               MANIFEST.name,        manifest_exists, manifest_size),
        ("MBTiles (Tippecanoe)",   MBTILES.name,         mbtiles_ok, mbtiles_size),
        ("PMTiles (browser)",      PMTILES.name,         pmtiles_ok, pmtiles_size),
    ]

    for label, fname, ok, size in files:
        tag = status_tag(ok, size)
        print(f"  {tag}  {label:25s}  {fname:45s}  {size:>10s}")

    # Manifest detail
    print()
    if manifest_exists:
        print(f"  Manifest keys checked: {len(REQUIRED_MANIFEST_KEYS)} required")
    else:
        print("  Manifest: cannot validate (file missing)")

    # Feature counts
    if manifest_exists:
        try:
            data = json.loads(MANIFEST.read_text())
            staged = data.get("staged_feature_count", "?")
            sources = data.get("sources", [])
            source_total = data.get("source_feature_count", "?")
            print(f"  Features:  {staged} staged  ({source_total} in source files, {len(sources)} forests)")
            print(f"  Layer:     {data.get('layer_name', '?')}")
            print(f"  Zoom:      {data.get('min_zoom', '?')} – {data.get('max_zoom', '?')}  (base: {data.get('base_zoom', '?')})")
        except (json.JSONDecodeError, OSError):
            pass

    # Overall status
    print()
    all_ok = staging_ok and manifest_exists and manifest_ok
    if all_ok and mbtiles_ok and pmtiles_ok:
        print("  Result: EVERYTHING AVAILABLE — preview ready.")
        print(f"  Open:   http://127.0.0.1:8080/vector_preview.html")
    elif all_ok and mbtiles_ok:
        print("  Result: STAGING + MBTILES OK — run pmtiles_convert.sh for browser preview.")
    elif all_ok:
        print("  Result: STAGING OK — run build_vector_preview_tiles.sh to generate tiles.")
    else:
        print("  Result: STAGING INCOMPLETE — run python3 scripts/build_vector_tile_staging.py")
    print()


def main() -> int:
    os.chdir(REPO_DIR)  # scripts may rely on cwd == repo root

    if not OUTPUT_DIR.exists():
        print(f"ERROR: output directory does not exist: {OUTPUT_DIR}")
        print("Run: python3 scripts/build_vector_tile_staging.py")
        return 1

    manifest_errors = check_manifest()
    manifest_ok = len(manifest_errors) == 0

    print_summary(manifest_ok)

    for err in manifest_errors:
        print(err)

    return 0 if manifest_ok else 1


if __name__ == "__main__":
    sys.exit(main())
