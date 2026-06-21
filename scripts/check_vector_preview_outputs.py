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
PUBLISH_DIR = REPO_DIR / "vector_tiles"

FOREST_FILES = {
    "staging": OUTPUT_DIR / "forest_roads_staging.geojson",
    "manifest": OUTPUT_DIR / "forest_roads_staging_manifest.json",
    "mbtiles": OUTPUT_DIR / "forest_roads.mbtiles",
    "pmtiles": OUTPUT_DIR / "forest_roads.pmtiles",
    "published_manifest": PUBLISH_DIR / "forest_roads_staging_manifest.json",
    "published_pmtiles": PUBLISH_DIR / "forest_roads.pmtiles",
}
PUBLIC_FILES = {
    "staging": OUTPUT_DIR / "public_roads_staging.geojson",
    "manifest": OUTPUT_DIR / "public_roads_staging_manifest.json",
    "mbtiles": OUTPUT_DIR / "public_roads.mbtiles",
    "pmtiles": OUTPUT_DIR / "public_roads.pmtiles",
    "published_manifest": PUBLISH_DIR / "public_roads_staging_manifest.json",
    "published_pmtiles": PUBLISH_DIR / "public_roads.pmtiles",
}

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


def check_manifest(path: Path, label: str) -> list[str]:
    errors: list[str] = []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        errors.append(f"  {label}: parse error — {exc}")
        return errors

    for key in REQUIRED_MANIFEST_KEYS:
        if key not in data:
            errors.append(f"  {label}: missing key \"{key}\"")

    # Validate types of numeric keys
    for key in ("min_zoom", "max_zoom", "base_zoom", "staged_feature_count"):
        val = data.get(key)
        if val is not None and not isinstance(val, (int, float)):
            errors.append(f"  {label}: \"{key}\" should be numeric, got {type(val).__name__}")

    if not isinstance(data.get("layer_name"), str) or not data["layer_name"].strip():
        errors.append(f"  {label}: \"layer_name\" must be a non-empty string")

    return errors


def status_tag(size: str) -> str:
    if "missing" in size:
        return "[MISSING]"
    if "empty" in size:
        return "[EMPTY] "
    return "[OK]     "


def print_dataset_summary(label: str, files: dict[str, Path], manifest_ok: bool) -> tuple[bool, bool, bool, bool]:
    staging_ok, staging_size = check_file(files["staging"], "staging GeoJSON")
    manifest_exists, manifest_size = check_file(files["manifest"], "manifest")
    mbtiles_ok, mbtiles_size = check_file(files["mbtiles"], "MBTiles")
    pmtiles_ok, pmtiles_size = check_file(files["pmtiles"], "PMTiles")
    published_manifest_ok, published_manifest_size = check_file(files["published_manifest"], "published manifest")
    published_pmtiles_ok, published_pmtiles_size = check_file(files["published_pmtiles"], "published PMTiles")

    print(f"  {label}")
    print("  " + "-" * len(label))
    rows = [
        ("staging GeoJSON", files["staging"].name, staging_size),
        ("manifest", files["manifest"].name, manifest_size),
        ("MBTiles (Tippecanoe)", files["mbtiles"].name, mbtiles_size),
        ("PMTiles (browser)", files["pmtiles"].name, pmtiles_size),
        ("Published manifest", files["published_manifest"].name, published_manifest_size),
        ("Published PMTiles", files["published_pmtiles"].name, published_pmtiles_size),
    ]
    for row_label, fname, size in rows:
        print(f"  {status_tag(size)}  {row_label:25s}  {fname:45s}  {size:>10s}")

    print()
    if manifest_exists:
        print(f"  Manifest keys checked: {len(REQUIRED_MANIFEST_KEYS)} required")
        try:
            data = json.loads(files["manifest"].read_text())
            staged = data.get("staged_feature_count", "?")
            source_total = data.get("source_feature_count", data.get("source_way_count", "?"))
            print(f"  Features:  {staged} staged  ({source_total} in source files)")
            print(f"  Layer:     {data.get('layer_name', '?')}")
            print(f"  Zoom:      {data.get('min_zoom', '?')} – {data.get('max_zoom', '?')}  (base: {data.get('base_zoom', '?')})")
        except (json.JSONDecodeError, OSError):
            pass
    else:
        print("  Manifest: cannot validate (file missing)")

    print()
    all_ok = staging_ok and manifest_exists and manifest_ok
    return all_ok, mbtiles_ok, pmtiles_ok, (published_manifest_ok and published_pmtiles_ok)


def print_summary(forest_manifest_ok: bool, public_manifest_ok: bool) -> None:
    print("=" * 56)
    print("  Vector-tile pipeline — output check")
    print("=" * 56)
    print("  Legend:  [OK]  [MISSING]  [EMPTY]  [?]")
    print()
    forest_all_ok, forest_mb_ok, forest_pm_ok, forest_pub_ok = print_dataset_summary(
        "Forest roads", FOREST_FILES, forest_manifest_ok
    )
    public_all_ok, public_mb_ok, public_pm_ok, public_pub_ok = print_dataset_summary(
        "Public roads", PUBLIC_FILES, public_manifest_ok
    )

    if forest_all_ok and public_all_ok and forest_mb_ok and public_mb_ok and forest_pm_ok and public_pm_ok and forest_pub_ok and public_pub_ok:
        print("  Result: EVERYTHING AVAILABLE — vector mode ready.")
        print("  Open:   http://127.0.0.1:8080/index.html?mode=vector")
        print("  Public: vector_tiles/ is ready for GitHub Pages.")
    elif forest_all_ok and public_all_ok and forest_mb_ok and public_mb_ok and forest_pm_ok and public_pm_ok:
        print("  Result: LOCAL TILE BUILD OK — publish copies are missing.")
    elif forest_all_ok and public_all_ok and forest_mb_ok and public_mb_ok:
        print("  Result: STAGING + MBTILES OK — run PMTiles conversion scripts.")
    elif forest_all_ok and public_all_ok:
        print("  Result: STAGING OK — run build_vector_preview_tiles.sh to generate tiles.")
    else:
        print("  Result: STAGING INCOMPLETE — build both staging datasets first.")
    print()


def main() -> int:
    os.chdir(REPO_DIR)  # scripts may rely on cwd == repo root

    if not OUTPUT_DIR.exists():
        print(f"ERROR: output directory does not exist: {OUTPUT_DIR}")
        print("Run: python3 scripts/build_vector_tile_staging.py")
        return 1

    forest_manifest_errors = check_manifest(FOREST_FILES["manifest"], "FOREST MANIFEST")
    public_manifest_errors = check_manifest(PUBLIC_FILES["manifest"], "PUBLIC MANIFEST")
    print_summary(len(forest_manifest_errors) == 0, len(public_manifest_errors) == 0)

    for err in forest_manifest_errors + public_manifest_errors:
        print(err)

    return 0 if not (forest_manifest_errors or public_manifest_errors) else 1


if __name__ == "__main__":
    sys.exit(main())
