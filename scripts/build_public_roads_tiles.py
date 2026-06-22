#!/usr/bin/env python3

import json
import math
from collections import defaultdict
from pathlib import Path

TILE_ZOOM = 10
SOURCE_DIR = Path("_public_roads_raw_tiles") / str(TILE_ZOOM)
OUTPUT_DIR = Path("_public_roads_tiles") / str(TILE_ZOOM)
MANIFEST_PATH = Path("public_roads_tiles_manifest.json")
PROPERTY_KEYS = (
    "name",
    "road_class",
    "surface",
    "tracktype",
    "motor_vehicle",
    "access",
    "source",
    "source_detail",
)


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


def round_point(point):
    return [round(float(point[0]), 5), round(float(point[1]), 5)]


def feature_tile_refs(coords, zoom):
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


def normalize_name(tags):
    return (
        tags.get("name")
        or tags.get("official_name")
        or tags.get("ref")
        or tags.get("unsigned_ref")
        or "(unnamed public road)"
    )


def normalize_feature(element, source_id):
    if element.get("type") != "way":
        return None
    geometry = element.get("geometry") or []
    if len(geometry) < 2:
        return None
    coords = [round_point((point["lon"], point["lat"])) for point in geometry]
    tags = element.get("tags") or {}
    surface_raw = tags.get("surface")
    props = {
        "name": normalize_name(tags),
        "road_class": tags.get("highway") or "",
        "surface": surface_raw or "",
        "tracktype": tags.get("tracktype") or "",
        "motor_vehicle": tags.get("motor_vehicle") or "",
        "access": tags.get("access") or "",
        "source": "OpenStreetMap",
        "source_detail": f"osm way {element['id']}",
        "source_tile": source_id,
    }
    if not surface_raw:
        props["surface_inferred"] = True

    road_class = props.get("road_class", "")
    if props.get("surface_inferred") and road_class in ("residential", "service", "road"):
        return None

    return {
        "id": element["id"],
        "coords": coords,
        "feature": {
            "type": "Feature",
            "properties": {k: v for k, v in props.items() if v not in ("", None)},
            "geometry": {
                "type": "LineString",
                "coordinates": coords,
            },
        },
    }


def clear_output_dir(path):
    if path.exists():
        for file_path in sorted(path.rglob("*.geojson"), reverse=True):
            file_path.unlink()
        for dir_path in sorted((item for item in path.rglob("*") if item.is_dir()), reverse=True):
            dir_path.rmdir()
    path.mkdir(parents=True, exist_ok=True)


def main():
    files = sorted(SOURCE_DIR.rglob("*.json"))
    if not files:
        raise SystemExit(f"No raw public-road JSON files found in {SOURCE_DIR}")

    ways_by_id = {}
    source_counts = {}

    for path in files:
        source_id = path.relative_to(SOURCE_DIR).with_suffix("").as_posix()
        data = json.loads(path.read_text())
        elements = data.get("elements") or []
        source_counts[source_id] = len(elements)
        for element in elements:
            normalized = normalize_feature(element, source_id)
            if not normalized:
                continue
            ways_by_id[normalized["id"]] = normalized

    clear_output_dir(OUTPUT_DIR)
    tile_features = defaultdict(list)
    tile_sources = defaultdict(set)

    for normalized in ways_by_id.values():
        refs = feature_tile_refs(normalized["coords"], TILE_ZOOM)
        for x, y in refs:
            tile_features[(x, y)].append(normalized["feature"])
            tile_sources[(x, y)].add(normalized["feature"]["properties"].get("source_tile", ""))

    manifest_tiles = {}
    for (x, y), features in sorted(tile_features.items()):
        out_dir = OUTPUT_DIR / str(x)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{y}.geojson"
        out_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")))
        key = f"{TILE_ZOOM}/{x}/{y}"
        manifest_tiles[key] = {
            "feature_count": len(features),
            "sources": sorted(source for source in tile_sources[(x, y)] if source),
        }

    manifest = {
        "tile_zoom": TILE_ZOOM,
        "tile_count": len(manifest_tiles),
        "unique_way_count": len(ways_by_id),
        "queried_tiles": sorted(source_counts.keys()),
        "tiles": manifest_tiles,
        "source_counts": source_counts,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(json.dumps({
        "tile_zoom": TILE_ZOOM,
        "tile_count": len(manifest_tiles),
        "unique_way_count": len(ways_by_id),
    }, indent=2))


if __name__ == "__main__":
    main()
