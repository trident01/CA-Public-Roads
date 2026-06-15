#!/usr/bin/env python3

import json
import math
from collections import defaultdict
from pathlib import Path

TILE_ZOOM = 10
SOURCE_DIR = Path("_roads_geojson")
OUTPUT_DIR = Path("_roads_tiles") / str(TILE_ZOOM)
MANIFEST_PATH = Path("roads_tiles_manifest.json")
PROPERTY_KEYS = ("symbol", "name", "districtname", "system", "surfacetype", "seasonal")


def clamp_lat(lat):
    return max(min(lat, 85.05112878), -85.05112878)


def lonlat_to_tile(lon, lat, zoom):
    lat = clamp_lat(lat)
    n = 2 ** zoom
    xtile = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    xtile = max(0, min(n - 1, xtile))
    ytile = max(0, min(n - 1, ytile))
    return xtile, ytile


def iter_lines(geometry):
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if geom_type == "LineString":
        yield coords
    elif geom_type == "MultiLineString":
        yield from coords


def round_point(point):
    return [round(float(point[0]), 5), round(float(point[1]), 5)]


def round_geometry(geometry):
    geom_type = geometry.get("type")
    if geom_type == "LineString":
        return {
            "type": "LineString",
            "coordinates": [round_point(point) for point in geometry.get("coordinates") or []]
        }
    if geom_type == "MultiLineString":
        return {
            "type": "MultiLineString",
            "coordinates": [
                [round_point(point) for point in line]
                for line in (geometry.get("coordinates") or [])
            ]
        }
    return None


def feature_tile_refs(geometry, zoom):
    coords = [point for line in iter_lines(geometry) for point in line]
    if len(coords) < 2:
        return set()

    lons = [point[0] for point in coords]
    lats = [point[1] for point in coords]
    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)

    min_x, max_y = lonlat_to_tile(min_lon, min_lat, zoom)
    max_x, min_y = lonlat_to_tile(max_lon, max_lat, zoom)

    refs = set()
    for x in range(min(min_x, max_x), max(min_x, max_x) + 1):
        for y in range(min(min_y, max_y), max(min_y, max_y) + 1):
            refs.add((x, y))
    return refs


def slim_feature(forest_id, feature):
    geometry = round_geometry(feature.get("geometry") or {})
    if not geometry:
        return None

    props = feature.get("properties") or {}
    out_props = {key: props.get(key) for key in PROPERTY_KEYS if props.get(key) not in (None, "")}
    out_props["forest_id"] = forest_id
    return {
        "type": "Feature",
        "properties": out_props,
        "geometry": geometry
    }


def main():
    files = sorted(SOURCE_DIR.glob("*.geojson"))
    if not files:
        raise SystemExit(f"No GeoJSON files found in {SOURCE_DIR}")

    tile_features = defaultdict(list)
    tile_forests = defaultdict(set)
    forest_counts = {}

    for path in files:
        forest_id = path.stem
        data = json.loads(path.read_text())
        features = data.get("features") or []
        forest_counts[forest_id] = len(features)

        for feature in features:
            slim = slim_feature(forest_id, feature)
            if not slim:
                continue
            refs = feature_tile_refs(slim["geometry"], TILE_ZOOM)
            for x, y in refs:
                tile_features[(x, y)].append(slim)
                tile_forests[(x, y)].add(forest_id)

    if OUTPUT_DIR.exists():
        for file_path in sorted(OUTPUT_DIR.rglob("*.geojson"), reverse=True):
            file_path.unlink()
        for dir_path in sorted((path for path in OUTPUT_DIR.rglob("*") if path.is_dir()), reverse=True):
            dir_path.rmdir()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest_tiles = {}

    for (x, y), features in sorted(tile_features.items()):
        out_dir = OUTPUT_DIR / str(x)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{y}.geojson"
        payload = {"type": "FeatureCollection", "features": features}
        out_path.write_text(json.dumps(payload, separators=(",", ":")))
        key = f"{TILE_ZOOM}/{x}/{y}"
        manifest_tiles[key] = {
            "feature_count": len(features),
            "forests": sorted(tile_forests[(x, y)])
        }

    manifest = {
        "tile_zoom": TILE_ZOOM,
        "tile_count": len(manifest_tiles),
        "tiles": manifest_tiles,
        "forest_counts": forest_counts
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(json.dumps({"tile_zoom": TILE_ZOOM, "tile_count": len(manifest_tiles)}, indent=2))


if __name__ == "__main__":
    main()
