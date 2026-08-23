#!/usr/bin/env python3
"""Build a conservative public-unpaved-road candidate layer.

Each output road must meet all of these conditions:
  * an explicit OpenStreetMap unpaved surface value (never inferred),
  * at least 500 m of mapped length,
  * at least 85% of its length lies within 30 m of Caltrans' All Roads network.

Caltrans describes All Roads as its federally required statewide all-public-roads
network. It has no surface field, so it is used only as the public-network
signal; OSM is used only as the explicit surface observation. The output keeps
the OSM geometry but records the Caltrans match evidence.

This is intentionally fail-closed. It is an opt-in candidate layer, not a
closure feed or a complete public-road inventory.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from shapely.geometry import LineString
from shapely.ops import unary_union
from shapely.strtree import STRtree

CALTRANS_QUERY_URL = (
    "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/"
    "All_Roads/MapServer/0/query"
)
INPUT_DIR = Path("_public_roads_tiles") / "10"
CACHE_DIR = Path("build") / "caltrans_all_roads_cache" / "10"
OUTPUT_DIR = Path("_verified_public_roads_tiles") / "10"
MANIFEST_PATH = Path("verified_public_roads_tiles_manifest.json")
STAGING_PATH = Path("build") / "vector_tiles" / "verified_public_roads_staging.geojson"
STAGING_MANIFEST_PATH = Path("build") / "vector_tiles" / "verified_public_roads_staging_manifest.json"
PUBLISHED_MANIFEST_PATH = Path("vector_tiles") / "verified_public_roads_staging_manifest.json"

TILE_ZOOM = 10
PAGE_SIZE = 2_000
MIN_LENGTH_M = 500.0
MATCH_DISTANCE_M = 30.0
MIN_MATCH_COVERAGE = 0.85
UNPAVED_SURFACES = frozenset({
    "dirt", "gravel", "ground", "unpaved", "sand", "earth", "mud", "clay",
    "grass", "fine_gravel", "pebblestone", "compacted", "cinder", "rock",
    "stone", "woodchips",
})


def clamp_lat(lat: float) -> float:
    return max(min(lat, 85.05112878), -85.05112878)


def lonlat_to_tile(lon: float, lat: float, zoom: int = TILE_ZOOM) -> tuple[int, int]:
    scale = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * scale)
    lat_rad = math.radians(clamp_lat(lat))
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * scale)
    return max(0, min(scale - 1, x)), max(0, min(scale - 1, y))


def tile_bounds(x: int, y: int, zoom: int = TILE_ZOOM) -> tuple[float, float, float, float]:
    scale = 2 ** zoom
    west = x / scale * 360.0 - 180.0
    east = (x + 1) / scale * 360.0 - 180.0
    north = math.degrees(math.atan(math.sinh(math.pi - 2.0 * math.pi * y / scale)))
    south = math.degrees(math.atan(math.sinh(math.pi - 2.0 * math.pi * (y + 1) / scale)))
    return west, south, east, north


def metric_line(coords: list[list[float]], lat0: float) -> LineString | None:
    if len(coords) < 2:
        return None
    sx = 111_320.0 * math.cos(math.radians(lat0))
    sy = 110_540.0
    line = LineString([(float(x) * sx, float(y) * sy) for x, y, *_ in coords])
    return line if line.length else None


def feature_length_m(coords: list[list[float]]) -> float:
    if len(coords) < 2:
        return 0.0
    lat0 = sum(point[1] for point in coords) / len(coords)
    line = metric_line(coords, lat0)
    return line.length if line else 0.0


def source_id(feature: dict) -> str:
    props = feature.get("properties") or {}
    return str(props.get("source_detail") or props.get("source_id") or "")


def in_bbox(coords: list[list[float]], bbox: tuple[float, float, float, float] | None) -> bool:
    if not bbox:
        return True
    west, south, east, north = bbox
    return any(west <= point[0] <= east and south <= point[1] <= north for point in coords)


def load_candidates(bbox: tuple[float, float, float, float] | None) -> dict[str, dict]:
    candidates: dict[str, dict] = {}
    for path in INPUT_DIR.rglob("*.geojson"):
        for feature in json.loads(path.read_text()).get("features") or []:
            props = feature.get("properties") or {}
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            key = source_id(feature)
            surface = str(props.get("surface") or "").lower()
            if not key or surface not in UNPAVED_SURFACES or not in_bbox(coords, bbox):
                continue
            length_m = feature_length_m(coords)
            if length_m < MIN_LENGTH_M:
                continue
            candidates[key] = {
                "feature": feature,
                "coords": coords,
                "length_m": length_m,
            }
    return candidates


def request_json(params: dict[str, str], attempts: int = 4) -> dict:
    request = urllib.request.Request(
        f"{CALTRANS_QUERY_URL}?{urllib.parse.urlencode(params)}",
        headers={"Accept": "application/json, application/geo+json", "User-Agent": "CA-Public-Roads verifier"},
    )
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.load(response)
            if payload.get("error"):
                raise RuntimeError(payload["error"])
            return payload
        except (OSError, urllib.error.URLError, json.JSONDecodeError, RuntimeError) as exc:
            error = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Caltrans All Roads request failed: {error}")


def fetch_caltrans_tile(x: int, y: int, refresh: bool) -> list[dict]:
    cache_path = CACHE_DIR / str(x) / f"{y}.geojson"
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text()).get("features") or []

    west, south, east, north = tile_bounds(x, y)
    # 40 m padding captures a route whose geometry falls just outside the tile.
    pad_lon = 0.0005
    pad_lat = 0.0004
    geometry = f"{west - pad_lon},{south - pad_lat},{east + pad_lon},{north + pad_lat}"
    count = request_json({
        "where": "1=1", "geometry": geometry, "geometryType": "esriGeometryEnvelope",
        "inSR": "4326", "spatialRel": "esriSpatialRelIntersects", "returnCountOnly": "true", "f": "json",
    }).get("count", 0)
    features: list[dict] = []
    for offset in range(0, int(count), PAGE_SIZE):
        payload = request_json({
            "where": "1=1", "geometry": geometry, "geometryType": "esriGeometryEnvelope",
            "inSR": "4326", "spatialRel": "esriSpatialRelIntersects", "outFields": "RouteId",
            "returnGeometry": "true", "outSR": "4326", "resultOffset": str(offset),
            "resultRecordCount": str(PAGE_SIZE), "orderByFields": "OBJECTID ASC", "f": "geojson",
        })
        page = payload.get("features") or []
        if not page:
            raise RuntimeError(f"Caltrans returned an empty page for {x}/{y} at offset {offset}")
        features.extend(page)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")))
    time.sleep(0.12)
    return features


def caltrans_lines(features: list[dict], lat0: float) -> list[LineString]:
    lines: list[LineString] = []
    for feature in features:
        geometry = feature.get("geometry") or {}
        kind = geometry.get("type")
        raw_lines = [geometry.get("coordinates") or []] if kind == "LineString" else geometry.get("coordinates") or []
        for coords in raw_lines:
            line = metric_line(coords, lat0)
            if line:
                lines.append(line)
    return lines


def verify_tile(candidates: list[dict], caltrans_features: list[dict]) -> list[dict]:
    if not candidates or not caltrans_features:
        return []
    coords = [point for candidate in candidates for point in candidate["coords"]]
    lat0 = sum(point[1] for point in coords) / len(coords)
    roads = caltrans_lines(caltrans_features, lat0)
    if not roads:
        return []
    tree = STRtree(roads)
    accepted: list[dict] = []
    for candidate in candidates:
        line = metric_line(candidate["coords"], lat0)
        if not line:
            continue
        nearby = tree.query(line, predicate="dwithin", distance=MATCH_DISTANCE_M)
        if not len(nearby):
            continue
        corridor = unary_union([roads[int(index)].buffer(MATCH_DISTANCE_M) for index in nearby])
        coverage = line.intersection(corridor).length / line.length
        if coverage < MIN_MATCH_COVERAGE:
            continue
        feature = candidate["feature"]
        props = dict(feature.get("properties") or {})
        osm_source_detail = str(props.get("source_detail") or "OpenStreetMap way")
        props.update({
            "source": "Caltrans All Roads + OpenStreetMap explicit surface",
            "source_detail": f"{osm_source_detail}; Caltrans All Roads public-network geometry match",
            "public_network_source": "Caltrans All Roads (ARNOLD/HPMS)",
            "surface_source": "OpenStreetMap explicit surface tag",
            "caltrans_match_coverage": round(coverage, 3),
            "verified_length_m": round(candidate["length_m"]),
        })
        accepted.append({"type": "Feature", "properties": props, "geometry": feature["geometry"]})
    return accepted


def feature_refs(coords: list[list[float]]) -> set[tuple[int, int]]:
    xs, ys = zip(*(lonlat_to_tile(point[0], point[1]) for point in coords))
    return {(x, y) for x in range(min(xs), max(xs) + 1) for y in range(min(ys), max(ys) + 1)}


def parse_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    if not value:
        return None
    west, south, east, north = (float(part) for part in value.split(","))
    if west >= east or south >= north:
        raise ValueError("bbox must be west,south,east,north")
    return west, south, east, north


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bbox", help="Optional pilot bbox: west,south,east,north")
    parser.add_argument("--refresh-caltrans-cache", action="store_true")
    args = parser.parse_args()
    bbox = parse_bbox(args.bbox)
    candidates = load_candidates(bbox)
    by_tile: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for candidate in candidates.values():
        lon = sum(point[0] for point in candidate["coords"]) / len(candidate["coords"])
        lat = sum(point[1] for point in candidate["coords"]) / len(candidate["coords"])
        by_tile[lonlat_to_tile(lon, lat)].append(candidate)

    verified: dict[str, dict] = {}
    for index, ((x, y), items) in enumerate(sorted(by_tile.items()), start=1):
        roads = fetch_caltrans_tile(x, y, args.refresh_caltrans_cache)
        for feature in verify_tile(items, roads):
            verified[source_id(feature)] = feature
        print(f"Verified {index}/{len(by_tile)} candidate tiles; accepted {len(verified):,} roads", flush=True)

    tile_features: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for feature in verified.values():
        for ref in feature_refs(feature["geometry"]["coordinates"]):
            tile_features[ref].append(feature)
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    for (x, y), features in tile_features.items():
        destination = OUTPUT_DIR / str(x)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / f"{y}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")))

    all_features = list(verified.values())
    surface_counts = Counter((feature["properties"].get("surface") or "unknown") for feature in all_features)
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    common = {
        "source_name": "Caltrans All Roads matched to OpenStreetMap explicit surface tags",
        "caltrans_source_url": "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/All_Roads/MapServer/0",
        "criteria": {
            "explicit_unpaved_surface_only": True,
            "minimum_length_m": MIN_LENGTH_M,
            "caltrans_match_distance_m": MATCH_DISTANCE_M,
            "minimum_caltrans_match_coverage": MIN_MATCH_COVERAGE,
        },
        "built_utc": now,
        "candidate_count": len(candidates),
        "verified_feature_count": len(all_features),
        "surface_counts": dict(sorted(surface_counts.items())),
        "scope_bbox": list(bbox) if bbox else None,
    }
    MANIFEST_PATH.write_text(json.dumps({
        **common, "tile_zoom": TILE_ZOOM, "tile_count": len(tile_features),
        "tiles": {f"{TILE_ZOOM}/{x}/{y}": {"feature_count": len(features)} for (x, y), features in sorted(tile_features.items())},
    }, indent=2, sort_keys=True))
    STAGING_PATH.parent.mkdir(parents=True, exist_ok=True)
    STAGING_PATH.write_text(json.dumps({"type": "FeatureCollection", "features": all_features}, separators=(",", ":")))
    vector_manifest = {
        **common, "layer_name": "verified_public_roads", "min_zoom": 11, "max_zoom": 14,
        "base_zoom": 12, "staged_feature_count": len(all_features),
        "staging_path": str(STAGING_PATH),
    }
    STAGING_MANIFEST_PATH.write_text(json.dumps(vector_manifest, indent=2, sort_keys=True))
    PUBLISHED_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PUBLISHED_MANIFEST_PATH.write_text(json.dumps(vector_manifest, indent=2, sort_keys=True))
    print(json.dumps({"candidates": len(candidates), "verified": len(all_features), "tiles": len(tile_features)}, indent=2))


if __name__ == "__main__":
    main()
