#!/usr/bin/env python3
"""
build_vector_tile_staging.py

Chunk 1 of the vector-tile migration.
Reads all forest-road GeoJSON files from _roads_geojson/, strips properties
to only those needed for styling/popups, and writes a combined staging file
suitable for Tippecanoe.

Output (writes to build/vector_tiles/):
  forest_roads_staging.geojson        – combined FeatureCollection
  forest_roads_staging_manifest.json – source / count metadata

Future step (after this script succeeds):
  tippecanoe -o build/vector_tiles/forest_roads.mbtiles ...
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# ── config ──────────────────────────────────────────────────────────────────
SOURCE_DIR = Path("_roads_geojson")
OUTPUT_DIR = Path("build") / "vector_tiles"
PUBLISH_DIR = Path("vector_tiles")
STAGING_FILENAME = "forest_roads_staging.geojson"
MANIFEST_FILENAME = "forest_roads_staging_manifest.json"
MBTILES_FILENAME = "forest_roads.mbtiles"
PMTILES_FILENAME = "forest_roads.pmtiles"
TIPPECANOE_LAYER = "forest_roads"
TIPPECANOE_MIN_ZOOM = 0
TIPPECANOE_MAX_ZOOM = 14
TIPPECANOE_BASE_ZOOM = 10
TIPPECANOE_FLAGS = (
    "--no-tile-compression",
    f"--layer={TIPPECANOE_LAYER}",
    f"--minimum-zoom={TIPPECANOE_MIN_ZOOM}",
    f"--maximum-zoom={TIPPECANOE_MAX_ZOOM}",
    f"--base-zoom={TIPPECANOE_BASE_ZOOM}",
    "--drop-densest-as-needed",
    "--extend-zooms-if-still-dropping",
)

# Allowlisted properties — only these will survive into the staging file.
# forest_id is added separately from the filename.
ALLOWED = frozenset({
    "name",
    "symbol",
    "surfacetype",
    "seasonal",
    "system",
    "districtname",
})


def slim_feature(forest_id: str, feature: dict) -> dict | None:
    """Return a new feature with only allowlisted properties + forest_id."""
    geometry = feature.get("geometry")
    if not geometry:
        return None

    props = feature.get("properties") or {}
    out_props = {"forest_id": forest_id}
    for key in ALLOWED:
        val = props.get(key)
        if val is not None and val != "":
            out_props[key] = val

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": out_props,
    }


def tippecanoe_command(staging_path: Path, mbtiles_path: Path) -> str:
    args = list(TIPPECANOE_FLAGS) + ["-o", str(mbtiles_path), str(staging_path)]
    return "tippecanoe " + " ".join(args)


def main() -> None:
    files = sorted(SOURCE_DIR.glob("*.geojson"))
    if not files:
        print(f"ERROR: no GeoJSON files found in {SOURCE_DIR}", file=sys.stderr)
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PUBLISH_DIR.mkdir(parents=True, exist_ok=True)

    all_features: list[dict] = []
    forest_stats: dict[str, dict[str, int]] = {}
    forest_sources: list[str] = []

    for path in files:
        forest_id = path.stem  # e.g. "eldorado_national_forest"
        forest_sources.append(forest_id)
        data = json.loads(path.read_text())
        features = data.get("features") or []
        source_count = len(features)
        staged_count = 0

        for feat in features:
            slim = slim_feature(forest_id, feat)
            if slim is not None:
                all_features.append(slim)
                staged_count += 1

        forest_stats[forest_id] = {
            "source_feature_count": source_count,
            "staged_feature_count": staged_count,
        }

    # ── write combined GeoJSON ──────────────────────────────────────────────
    staging_path = OUTPUT_DIR / STAGING_FILENAME
    collection = {
        "type": "FeatureCollection",
        "features": all_features,
    }
    staging_path.write_text(json.dumps(collection, separators=(",", ":")))
    print(f"Wrote {len(all_features)} features -> {staging_path}")

    # ── write manifest ──────────────────────────────────────────────────────
    manifest_path = OUTPUT_DIR / MANIFEST_FILENAME
    mbtiles_path = OUTPUT_DIR / MBTILES_FILENAME
    manifest = {
        "description": "Staging file for vector-tile build pipeline (Chunk 1)",
        "sources": sorted(forest_sources),
        "forest_stats": {k: forest_stats[k] for k in sorted(forest_stats)},
        "source_feature_count": sum(item["source_feature_count"] for item in forest_stats.values()),
        "staged_feature_count": len(all_features),
        "allowlisted_properties": sorted(ALLOWED),
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "layer_name": TIPPECANOE_LAYER,
        "min_zoom": TIPPECANOE_MIN_ZOOM,
        "max_zoom": TIPPECANOE_MAX_ZOOM,
        "base_zoom": TIPPECANOE_BASE_ZOOM,
        "tippecanoe_flags": list(TIPPECANOE_FLAGS),
        "staging_path": str(staging_path),
        "manifest_path": str(manifest_path),
        "mbtiles_path": str(mbtiles_path),
        "pmtiles_path": str(OUTPUT_DIR / PMTILES_FILENAME),
        "tippecanoe_command": tippecanoe_command(staging_path, mbtiles_path),
    }

    # Compute approximate bounding box from feature geometries
    lons, lats = [], []
    for feat in all_features:
        geom = feat.get("geometry")
        if not geom:
            continue
        coords = _extract_coords(geom)
        for lon, lat in coords:
            lons.append(lon)
            lats.append(lat)
    if lons and lats:
        manifest["bounds"] = [min(lons), min(lats), max(lons), max(lats)]

    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"Wrote manifest -> {manifest_path}")

    publish_manifest_path = PUBLISH_DIR / MANIFEST_FILENAME
    publish_manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"Wrote published manifest -> {publish_manifest_path}")


def _extract_coords(geom: dict) -> list[tuple[float, float]]:
    """Yield (lon, lat) pairs from a LineString or MultiLineString."""
    t = geom.get("type")
    coords = geom.get("coordinates") or []
    if t == "LineString":
        return [(c[0], c[1]) for c in coords]
    if t == "MultiLineString":
        return [(c[0], c[1]) for line in coords for c in line]
    return []


if __name__ == "__main__":
    main()
