#!/usr/bin/env python3
"""Fetch and build the conservative BLM public-motorized road layer.

The source is BLM's public-display GTLF layer 0. The server-side query and the
local validator both require an explicitly unpaved surface and an observed
full-size vehicle class. Routes with administrative or permit-only access are
rejected. This script writes classic GeoJSON tiles plus vector-tile staging.
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

SERVICE_ROOT = (
    "https://gis.blm.gov/arcgis/rest/services/transportation/"
    "BLM_Natl_GTLF_Public_Display/MapServer"
)
LAYER_URL = f"{SERVICE_ROOT}/0"
QUERY_URL = f"{LAYER_URL}/query"
PAGE_SIZE = 2000
TILE_ZOOM = 10
TILE_OUTPUT_DIR = Path("_blm_roads_tiles") / str(TILE_ZOOM)
TILE_MANIFEST_PATH = Path("blm_public_roads_tiles_manifest.json")
VECTOR_OUTPUT_DIR = Path("build") / "vector_tiles"
VECTOR_PUBLISH_DIR = Path("vector_tiles")
VECTOR_STAGING_PATH = VECTOR_OUTPUT_DIR / "blm_public_roads_staging.geojson"
VECTOR_MANIFEST_PATH = VECTOR_OUTPUT_DIR / "blm_public_roads_staging_manifest.json"
VECTOR_PUBLISHED_MANIFEST_PATH = VECTOR_PUBLISH_DIR / "blm_public_roads_staging_manifest.json"
VECTOR_LAYER_NAME = "blm_public_roads"
VECTOR_MIN_ZOOM = 5
VECTOR_MAX_ZOOM = 14
VECTOR_BASE_ZOOM = 10

ALLOWED_SURFACES = frozenset({"NATURAL", "NATURAL IMPROVED", "AGGREGATE"})
ALLOWED_VEHICLE_CLASSES = frozenset({
    "2WD LOW",
    "4WD LOW",
    "4WD HIGH CLEARANCE / SPECIALIZED",
})
BLOCKED_ACCESS_RESTRICTIONS = frozenset({
    "AUTHORIZED/PERMITTED USER ONLY",
    "ADMIN ONLY",
    "ALL",
})

STRICT_WHERE = (
    "ADMIN_ST='CA' "
    "AND OBSRVE_SRFCE_TYPE IN ('NATURAL','NATURAL IMPROVED','AGGREGATE') "
    "AND OBSRVE_ROUTE_USE_CLASS IN "
    "('2WD LOW','4WD LOW','4WD HIGH CLEARANCE / SPECIALIZED') "
    "AND (PLAN_ACCESS_RSTRCT IS NULL OR PLAN_ACCESS_RSTRCT NOT IN "
    "('AUTHORIZED/PERMITTED USER ONLY','ADMIN ONLY','ALL'))"
)

OUT_FIELDS = ",".join((
    "OBJECTID",
    "GlobalID",
    "ADMIN_ST",
    "PLAN_ROUTE_DSGNTN_AUTH",
    "PLAN_ASSET_CLASS",
    "PLAN_OHV_ROUTE_DSGNTN",
    "OHV_ROUTE_DSGNTN_LIM",
    "OHV_DSGNTN_LIM_EXPLAIN",
    "PLAN_MODE_TRNSPRT",
    "PLAN_ALLOW_MODE_TRNSPRT",
    "PLAN_ACCESS_RSTRCT",
    "PLAN_SEASON_RSTRCT_CODE",
    "OBSRVE_SRFCE_TYPE",
    "OBSRVE_ROUTE_USE_CLASS",
    "ROUTE_PRMRY_NM",
    "ROUTE_SCNDRY_SPCL_DSGNTN_NM",
    "ROUTE_PLAN_ID",
    "TMA_ID",
    "GIS_MILES",
))


def request_json(params: dict[str, str], attempts: int = 4) -> dict:
    url = f"{QUERY_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, application/geo+json",
            "User-Agent": "CA-Forest-Service-Roads BLM static tile builder",
        },
    )
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.load(response)
            if "error" in payload:
                raise RuntimeError(f"BLM service error: {payload['error']}")
            return payload
        except (OSError, urllib.error.URLError, json.JSONDecodeError, RuntimeError) as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"BLM request failed after {attempts} attempts: {last_error}")


def fetch_features() -> tuple[list[dict], int]:
    count_payload = request_json({
        "where": STRICT_WHERE,
        "returnCountOnly": "true",
        "f": "json",
    })
    expected_count = int(count_payload.get("count", 0))
    if expected_count <= 0:
        raise RuntimeError("BLM query unexpectedly returned zero features")

    features: list[dict] = []
    for offset in range(0, expected_count, PAGE_SIZE):
        payload = request_json({
            "where": STRICT_WHERE,
            "outFields": OUT_FIELDS,
            "returnGeometry": "true",
            "outSR": "4326",
            "orderByFields": "OBJECTID ASC",
            "resultOffset": str(offset),
            "resultRecordCount": str(PAGE_SIZE),
            "f": "geojson",
        })
        page = payload.get("features") or []
        if not page:
            raise RuntimeError(f"BLM pagination returned an empty page at offset {offset}")
        features.extend(page)
        print(f"Fetched {min(len(features), expected_count):,}/{expected_count:,} BLM segments")

    if len(features) != expected_count:
        raise RuntimeError(
            f"BLM feature count mismatch: expected {expected_count}, fetched {len(features)}"
        )
    return features, expected_count


def iter_lines(geometry: dict):
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if geom_type == "LineString":
        yield coords
    elif geom_type == "MultiLineString":
        yield from coords


def round_geometry(geometry: dict) -> dict | None:
    lines = []
    for line in iter_lines(geometry):
        rounded = [[round(float(point[0]), 5), round(float(point[1]), 5)] for point in line]
        if len(rounded) >= 2:
            lines.append(rounded)
    if not lines:
        return None
    if geometry.get("type") == "LineString" and len(lines) == 1:
        return {"type": "LineString", "coordinates": lines[0]}
    return {"type": "MultiLineString", "coordinates": lines}


def normalized_value(value: object) -> str:
    return str(value or "").strip()


def normalized_key(value: object) -> str:
    return normalized_value(value).lower().replace(" / ", "_").replace(" ", "_")


def normalize_feature(feature: dict) -> dict | None:
    props = feature.get("properties") or {}
    geometry = round_geometry(feature.get("geometry") or {})
    if not geometry:
        return None

    surface = normalized_value(props.get("OBSRVE_SRFCE_TYPE")).upper()
    vehicle_class = normalized_value(props.get("OBSRVE_ROUTE_USE_CLASS")).upper()
    access_restriction = normalized_value(props.get("PLAN_ACCESS_RSTRCT")).upper()
    state = normalized_value(props.get("ADMIN_ST")).upper()
    authority = normalized_value(props.get("PLAN_ROUTE_DSGNTN_AUTH")).upper()
    route_designation = normalized_value(props.get("PLAN_OHV_ROUTE_DSGNTN")).upper()
    planned_mode = normalized_value(props.get("PLAN_MODE_TRNSPRT")).upper()

    if state != "CA":
        return None
    if authority != "BLM" or route_designation != "OPEN" or planned_mode != "MOTORIZED":
        return None
    if surface not in ALLOWED_SURFACES or vehicle_class not in ALLOWED_VEHICLE_CLASSES:
        return None
    if access_restriction in BLOCKED_ACCESS_RESTRICTIONS:
        return None

    all_coords = [point for line in iter_lines(geometry) for point in line]
    if any(not (-124.6 <= point[0] <= -114.0 and 32.4 <= point[1] <= 42.1) for point in all_coords):
        return None

    object_id = props.get("OBJECTID")
    global_id = normalized_value(props.get("GlobalID"))
    primary_name = normalized_value(props.get("ROUTE_PRMRY_NM"))
    secondary_name = normalized_value(props.get("ROUTE_SCNDRY_SPCL_DSGNTN_NM"))
    name = primary_name or secondary_name or "Unnamed BLM route"

    output_props = {
        "name": name,
        "source": "Bureau of Land Management",
        "source_detail": f"BLM GTLF OBJECTID {object_id}",
        "source_id": global_id or str(object_id),
        "road_class": normalized_key(props.get("PLAN_ASSET_CLASS")),
        "surface": normalized_key(surface),
        "vehicle_class": normalized_key(vehicle_class),
        "allowed_mode": normalized_key(props.get("PLAN_ALLOW_MODE_TRNSPRT")),
        "access_restriction": normalized_key(access_restriction) or "not_listed",
        "seasonal_restriction": normalized_key(props.get("PLAN_SEASON_RSTRCT_CODE")) or "not_listed",
        "restriction_type": normalized_key(props.get("OHV_ROUTE_DSGNTN_LIM")),
        "restriction_detail": normalized_value(props.get("OHV_DSGNTN_LIM_EXPLAIN")),
        "route_plan_id": normalized_value(props.get("ROUTE_PLAN_ID")),
        "travel_management_area": normalized_value(props.get("TMA_ID")),
        "gis_miles": props.get("GIS_MILES"),
    }
    output_props = {key: value for key, value in output_props.items() if value not in (None, "")}
    return {"type": "Feature", "properties": output_props, "geometry": geometry}


def clamp_lat(lat: float) -> float:
    return max(min(lat, 85.05112878), -85.05112878)


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    lat = clamp_lat(lat)
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def feature_tile_refs(geometry: dict) -> set[tuple[int, int]]:
    coords = [point for line in iter_lines(geometry) for point in line]
    lons = [point[0] for point in coords]
    lats = [point[1] for point in coords]
    min_x, max_y = lonlat_to_tile(min(lons), min(lats), TILE_ZOOM)
    max_x, min_y = lonlat_to_tile(max(lons), max(lats), TILE_ZOOM)
    return {
        (x, y)
        for x in range(min(min_x, max_x), max(min_x, max_x) + 1)
        for y in range(min(min_y, max_y), max(min_y, max_y) + 1)
    }


def bounds(features: list[dict]) -> list[float]:
    coords = [
        point
        for feature in features
        for line in iter_lines(feature["geometry"])
        for point in line
    ]
    return [
        min(point[0] for point in coords),
        min(point[1] for point in coords),
        max(point[0] for point in coords),
        max(point[1] for point in coords),
    ]


def build_outputs(source_features: list[dict], expected_count: int) -> None:
    normalized_by_id: dict[str, dict] = {}
    rejected_count = 0
    for feature in source_features:
        normalized = normalize_feature(feature)
        if not normalized:
            rejected_count += 1
            continue
        normalized_by_id[normalized["properties"]["source_id"]] = normalized

    features = list(normalized_by_id.values())
    if len(features) != expected_count:
        raise RuntimeError(
            "Fail-closed BLM validation rejected or deduplicated features: "
            f"expected {expected_count}, staged {len(features)}, rejected {rejected_count}"
        )

    fetched_utc = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    total_miles = sum(float(feature["properties"].get("gis_miles") or 0) for feature in features)
    surface_counts = Counter(feature["properties"]["surface"] for feature in features)
    vehicle_counts = Counter(feature["properties"]["vehicle_class"] for feature in features)
    data_bounds = bounds(features)

    tile_features: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for feature in features:
        for ref in feature_tile_refs(feature["geometry"]):
            tile_features[ref].append(feature)

    if TILE_OUTPUT_DIR.exists():
        shutil.rmtree(TILE_OUTPUT_DIR)
    TILE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_tiles = {}
    for (x, y), items in sorted(tile_features.items()):
        out_dir = TILE_OUTPUT_DIR / str(x)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{y}.geojson").write_text(json.dumps({
            "type": "FeatureCollection",
            "features": items,
        }, separators=(",", ":")))
        manifest_tiles[f"{TILE_ZOOM}/{x}/{y}"] = {"feature_count": len(items)}

    common_manifest = {
        "source_name": "BLM National Ground Transportation Linear Features Public Display",
        "source_layer": "Roads Managed for Public Motorized Use",
        "source_url": LAYER_URL,
        "source_where": STRICT_WHERE,
        "fetched_utc": fetched_utc,
        "unique_feature_count": len(features),
        "total_gis_miles": round(total_miles, 3),
        "surface_counts": dict(sorted(surface_counts.items())),
        "vehicle_class_counts": dict(sorted(vehicle_counts.items())),
        "bounds": data_bounds,
    }
    tile_manifest = {
        **common_manifest,
        "tile_zoom": TILE_ZOOM,
        "tile_count": len(manifest_tiles),
        "tiles": manifest_tiles,
    }
    TILE_MANIFEST_PATH.write_text(json.dumps(tile_manifest, indent=2, sort_keys=True))

    VECTOR_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    VECTOR_PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    VECTOR_STAGING_PATH.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": features,
    }, separators=(",", ":")))
    vector_manifest = {
        **common_manifest,
        "description": "Strict BLM public-motorized unpaved-road vector staging",
        "source_feature_count": expected_count,
        "staged_feature_count": len(features),
        "layer_name": VECTOR_LAYER_NAME,
        "min_zoom": VECTOR_MIN_ZOOM,
        "max_zoom": VECTOR_MAX_ZOOM,
        "base_zoom": VECTOR_BASE_ZOOM,
        "staging_path": str(VECTOR_STAGING_PATH),
        "manifest_path": str(VECTOR_MANIFEST_PATH),
        "mbtiles_path": str(VECTOR_OUTPUT_DIR / "blm_public_roads.mbtiles"),
        "pmtiles_path": str(VECTOR_OUTPUT_DIR / "blm_public_roads.pmtiles"),
        "tippecanoe_command": (
            "tippecanoe --force --no-tile-compression --layer=blm_public_roads "
            "--minimum-zoom=5 --maximum-zoom=14 --base-zoom=10 "
            "--drop-densest-as-needed --extend-zooms-if-still-dropping "
            "-o build/vector_tiles/blm_public_roads.mbtiles "
            "build/vector_tiles/blm_public_roads_staging.geojson"
        ),
    }
    VECTOR_MANIFEST_PATH.write_text(json.dumps(vector_manifest, indent=2, sort_keys=True))
    VECTOR_PUBLISHED_MANIFEST_PATH.write_text(json.dumps(vector_manifest, indent=2, sort_keys=True))

    print(json.dumps({
        "features": len(features),
        "miles": round(total_miles, 1),
        "classic_tiles": len(manifest_tiles),
        "bounds": data_bounds,
    }, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        help="Optional ArcGIS GeoJSON response for offline/reproducibility testing",
    )
    args = parser.parse_args()

    if args.input:
        payload = json.loads(args.input.read_text())
        source_features = payload.get("features") or []
        expected_count = len(source_features)
    else:
        source_features, expected_count = fetch_features()
    build_outputs(source_features, expected_count)


if __name__ == "__main__":
    main()
