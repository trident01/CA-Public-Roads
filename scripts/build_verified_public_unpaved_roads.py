#!/usr/bin/env python3
"""Build a conservative unpaved-road candidate layer.

Each output road must meet all of these conditions:
  * an explicit OpenStreetMap unpaved surface value (never inferred),
  * at least 500 m of mapped length,
  * at least 85% of its length lies within 30 m of Caltrans' All Roads network.

Caltrans describes All Roads as its federally required statewide all-public-roads
network. It has no surface field. A proximity match can also be a nearby,
parallel road, so it does not verify road identity, current surface, or legal
bicycle/foot access. The output keeps the OSM geometry and records this limited
match evidence. The default input is restricted to padded forest bounding
boxes by build_public_roads_tiles.py; it is not a statewide inventory.

This is intentionally fail-closed. It is an opt-in candidate layer, not a
closure feed or a complete public-road inventory.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from shapely.geometry import LineString, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

CALTRANS_QUERY_URL = (
    "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/"
    "All_Roads/MapServer/0/query"
)
INPUT_DIR = Path("_public_roads_tiles") / "10"
INPUT_MANIFEST_PATH = Path("public_roads_tiles_manifest.json")
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
BLOCKED_ACCESS_VALUES = frozenset({
    "no", "private", "destination", "permit", "permit_required", "agricultural", "forestry",
    "customers", "delivery", "military", "employees", "restricted", "official", "emergency",
})
PAVED_SURFACES = frozenset({
    "paved", "asphalt", "concrete", "concrete:lanes", "concrete:plates",
    "paving_stones", "sett", "cobblestone", "unhewn_cobblestone",
})
POSITIVE_RECREATION_ACCESS = frozenset({"yes", "designated", "permissive", "official"})
_CACHE_LOCKS_GUARD = threading.Lock()
_CACHE_LOCKS: dict[tuple[int, int], threading.Lock] = {}
_REFRESHED_TILES: set[tuple[int, int]] = set()


def candidate_properties_allowed(props: dict) -> bool:
    """Require explicit surface evidence, independently of upstream filtering."""
    # Preserve the existing whitelist: do not infer surfaces from tracktype.
    if str(props.get("surface") or "").lower() not in UNPAVED_SURFACES:
        return False
    if str(props.get("surface_inferred") or "").strip().lower() in {"true", "1", "yes"}:
        return False
    access = str(props.get("access") or "").strip().lower()
    if any(value.strip() in BLOCKED_ACCESS_VALUES for value in access.split(";")):
        return False
    mode_values = {
        key: {value.strip().lower() for value in str(props.get(key) or "").split(";")}
        for key in ("bicycle", "foot", "vehicle", "motor_vehicle")
    }
    if all(mode_values[key] & BLOCKED_ACCESS_VALUES for key in ("bicycle", "foot")):
        return False
    if (any(mode_values[key] & BLOCKED_ACCESS_VALUES for key in ("vehicle", "motor_vehicle"))
            and not any(mode_values[key] & POSITIVE_RECREATION_ACCESS for key in ("bicycle", "foot"))):
        return False
    if str(props.get("paved") or "").strip().lower() in {"yes", "true", "1"}:
        return False
    for key, value in props.items():
        if not str(key).startswith("surface:"):
            continue
        # Directional/lane surface tags may contradict the general tag. A
        # conditionally paved segment is also unsuitable for this strict layer.
        materials = re.split(r"[;|]", str(value).lower())
        if any(material.split("@", 1)[0].strip() in PAVED_SURFACES for material in materials):
            return False
    return True


def valid_line_coords(coords: object) -> bool:
    """Reject the whole line rather than joining across malformed vertices."""
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return False
    for point in coords:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return False
        lon, lat = point[:2]
        if (isinstance(lon, bool) or isinstance(lat, bool)
                or not isinstance(lon, (int, float)) or not isinstance(lat, (int, float))
                or not math.isfinite(lon) or not math.isfinite(lat)
                or not -180 <= lon <= 180 or not -90 <= lat <= 90):
            return False
    return True


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
    if not valid_line_coords(coords) or not math.isfinite(lat0) or not -90 <= lat0 <= 90:
        return None
    sx = 111_320.0 * math.cos(math.radians(lat0))
    sy = 110_540.0
    line = LineString([(float(x) * sx, float(y) * sy) for x, y, *_ in coords])
    return line if line.is_valid and math.isfinite(line.length) and line.length > 0 else None


def feature_length_m(coords: list[list[float]]) -> float:
    if not valid_line_coords(coords):
        return 0.0
    lat0 = sum(point[1] for point in coords) / len(coords)
    line = metric_line(coords, lat0)
    return line.length if line else 0.0


def source_id(feature: dict) -> str:
    props = feature.get("properties") or {}
    for value in (props.get("osm_way_id"), props.get("source_id"), feature.get("id")):
        if value is not None and str(value).isdigit():
            return str(value)
    detail = str(props.get("source_detail") or "")
    match = re.search(r"\b(?:osm|OpenStreetMap)\s+way\s+(\d+)\b", detail, re.IGNORECASE)
    return match.group(1) if match else detail or str(props.get("source_id") or "")


def in_bbox(coords: list[list[float]], bbox: tuple[float, float, float, float] | None) -> bool:
    if not bbox:
        return True
    west, south, east, north = bbox
    return any(west <= point[0] <= east and south <= point[1] <= north for point in coords)


def prepare_candidate(feature: dict, bbox: tuple[float, float, float, float] | None = None) -> dict | None:
    if not isinstance(feature, dict):
        return None
    props = feature.get("properties") or {}
    geometry = feature.get("geometry") or {}
    if not isinstance(props, dict) or not isinstance(geometry, dict):
        return None
    coords = geometry.get("coordinates") or []
    if (not source_id(feature) or not candidate_properties_allowed(props)
            or geometry.get("type") != "LineString" or not valid_line_coords(coords)
            or not in_bbox(coords, bbox)):
        return None
    length_m = feature_length_m(coords)
    if length_m < MIN_LENGTH_M:
        return None
    return {"feature": feature, "coords": coords, "length_m": length_m}


def load_candidates(
    bbox: tuple[float, float, float, float] | None,
    input_file: Path | None = None,
    input_dir: Path = INPUT_DIR,
) -> dict[str, dict]:
    candidates: dict[str, dict] = {}
    paths = [input_file] if input_file else sorted(input_dir.rglob("*.geojson"))
    for path in paths:
        for feature in json.loads(path.read_text()).get("features") or []:
            candidate = prepare_candidate(feature, bbox)
            if candidate:
                candidates[source_id(feature)] = candidate
    return candidates


def input_provenance(input_file: Path | None, input_dir: Path, input_manifest: Path | None) -> dict:
    legacy_input = input_file is None and input_dir == INPUT_DIR
    manifest_path = input_manifest or (INPUT_MANIFEST_PATH if legacy_input else None)
    manifest = json.loads(manifest_path.read_text()) if manifest_path else {}
    if legacy_input:
        scope = {
            "kind": "padded_forest_bounding_boxes",
            "description": "Roads intersecting California forest bounding boxes padded by 0.1 degrees; not statewide coverage or exact forest boundaries",
            "forest_bbox_count": manifest.get("forest_bbox_count"),
            "forest_bbox_pad_degrees": manifest.get("forest_bbox_pad_degrees", 0.1),
        }
    else:
        scope = manifest.get("scope") or {
            "kind": "custom_input",
            "description": "Custom input; geographic completeness has not been established",
        }
    return {
        "input_path": str(input_file or input_dir),
        "input_manifest_path": str(manifest_path) if manifest_path else None,
        "input_scope": scope,
        "input_source_metadata": manifest if not legacy_input else {},
    }


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
    ref = (x, y)
    with _CACHE_LOCKS_GUARD:
        lock = _CACHE_LOCKS.setdefault(ref, threading.Lock())
    with lock:
        return _fetch_caltrans_tile(x, y, refresh and ref not in _REFRESHED_TILES)


def validate_caltrans_page_count(features: list[dict], expected_count: int) -> None:
    if (isinstance(expected_count, bool) or not isinstance(expected_count, int)
            or expected_count < 0 or len(features) != expected_count):
        raise RuntimeError(f"Incomplete Caltrans response: expected {expected_count} features, received {len(features)}")
    object_ids = [(feature.get("properties") or {}).get("OBJECTID") for feature in features]
    if all(value is not None for value in object_ids) and len(set(object_ids)) != len(object_ids):
        raise RuntimeError("Caltrans response contains duplicate OBJECTIDs")


def _fetch_caltrans_tile(x: int, y: int, refresh: bool) -> list[dict]:
    cache_path = CACHE_DIR / str(x) / f"{y}.geojson"
    if cache_path.exists() and not refresh:
        cached = json.loads(cache_path.read_text())
        features = cached.get("features") or []
        if "expected_feature_count" in cached:
            validate_caltrans_page_count(features, cached["expected_feature_count"])
        return features

    west, south, east, north = tile_bounds(x, y)
    # 40 m padding captures a route whose geometry falls just outside the tile.
    pad_lon = 0.0005
    pad_lat = 0.0004
    geometry = f"{west - pad_lon},{south - pad_lat},{east + pad_lon},{north + pad_lat}"
    count = request_json({
        "where": "1=1", "geometry": geometry, "geometryType": "esriGeometryEnvelope",
        "inSR": "4326", "spatialRel": "esriSpatialRelIntersects", "returnCountOnly": "true", "f": "json",
    }).get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise RuntimeError("Caltrans returned an invalid feature count")
    features: list[dict] = []
    for offset in range(0, int(count), PAGE_SIZE):
        payload = request_json({
            "where": "1=1", "geometry": geometry, "geometryType": "esriGeometryEnvelope",
            "inSR": "4326", "spatialRel": "esriSpatialRelIntersects", "outFields": "OBJECTID,RouteId",
            "returnGeometry": "true", "outSR": "4326", "resultOffset": str(offset),
            "resultRecordCount": str(PAGE_SIZE), "orderByFields": "OBJECTID ASC", "f": "geojson",
        })
        page = payload.get("features") or []
        if not page:
            raise RuntimeError(f"Caltrans returned an empty page for {x}/{y} at offset {offset}")
        features.extend(page)
    validate_caltrans_page_count(features, count)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=cache_path.parent, prefix=cache_path.name, suffix=".tmp", delete=False) as output:
            temporary_path = Path(output.name)
            json.dump({"type": "FeatureCollection", "features": features,
                       "fetched_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                       "expected_feature_count": count}, output, separators=(",", ":"))
        temporary_path.replace(cache_path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()
    _REFRESHED_TILES.add((x, y))
    time.sleep(0.12)
    return features


def caltrans_lines(features: list[dict], lat0: float) -> list[LineString]:
    lines: list[LineString] = []
    for feature in features:
        geometry = feature.get("geometry") or {}
        if not isinstance(geometry, dict):
            continue
        kind = geometry.get("type")
        if kind not in {"LineString", "MultiLineString"}:
            continue
        raw_lines = [geometry.get("coordinates") or []] if kind == "LineString" else geometry.get("coordinates") or []
        if not isinstance(raw_lines, (list, tuple)):
            continue
        for coords in raw_lines:
            line = metric_line(coords, lat0)
            if line:
                lines.append(line)
    return lines


def verify_tile(candidates: list[dict], caltrans_features: list[dict]) -> list[dict]:
    # Do not rely on the caller or the upstream staging builder for eligibility.
    candidates = [prepared for candidate in candidates
                  if (prepared := prepare_candidate(candidate["feature"])) is not None]
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
            "source_detail": f"{osm_source_detail}; Caltrans All Roads geometry proximity match",
            "public_network_source": "Caltrans All Roads (ARNOLD/HPMS)",
            "surface_source": "OpenStreetMap explicit surface tag",
            "caltrans_match_coverage": round(coverage, 3),
            "verified_length_m": round(candidate["length_m"]),
            "osm_way_id": source_id(feature),
            "access_status": "not_verified",
            "match_evidence": "geometry_proximity_only",
        })
        accepted.append({"type": "Feature", "properties": props, "geometry": feature["geometry"]})
    return accepted


def feature_refs(coords: list[list[float]]) -> set[tuple[int, int]]:
    if not valid_line_coords(coords):
        return set()
    xs, ys = zip(*(lonlat_to_tile(point[0], point[1]) for point in coords))
    return {(x, y) for x in range(min(xs), max(xs) + 1) for y in range(min(ys), max(ys) + 1)}


def matching_tile_refs(coords: list[list[float]]) -> set[tuple[int, int]]:
    """Cover the actual line, including sections outside its centroid tile."""
    if not valid_line_coords(coords):
        return set()
    line = LineString([(point[0], point[1]) for point in coords])
    return {ref for ref in feature_refs(coords) if line.intersects(box(*tile_bounds(*ref)))}


def caltrans_cache_provenance(candidates: list[dict]) -> dict:
    refs = {ref for candidate in candidates for ref in matching_tile_refs(candidate["coords"])}
    dates = []
    fallback_count = 0
    for x, y in refs:
        path = CACHE_DIR / str(x) / f"{y}.geojson"
        with path.open("rb") as source:
            source.seek(max(0, path.stat().st_size - 500))
            match = re.search(r'"fetched_utc":"([^"]+)"', source.read().decode())
        if match:
            dates.append(datetime.fromisoformat(match.group(1)))
        else:
            dates.append(datetime.fromtimestamp(path.stat().st_mtime, UTC))
            fallback_count += 1
    return {
        "caltrans_geometry_cache_fetched_utc_range": [min(dates).isoformat(), max(dates).isoformat()],
        "caltrans_geometry_cache_tile_count": len(refs),
        "caltrans_cache_timestamp_method": "Recorded fetched_utc; filesystem cache-write time for caches created before timestamp recording",
        "caltrans_cache_timestamp_fallback_tile_count": fallback_count,
    }


def fetch_candidate_caltrans_features(candidates: list[dict], refresh: bool) -> list[dict]:
    refs = {ref for candidate in candidates for ref in matching_tile_refs(candidate["coords"])}
    features = {}
    for x, y in sorted(refs):
        for feature in fetch_caltrans_tile(x, y, refresh):
            props = feature.get("properties") or {}
            object_id = props.get("OBJECTID")
            key = ("object_id", str(object_id)) if object_id is not None else (
                "geometry", json.dumps(feature.get("geometry"), sort_keys=True, separators=(",", ":")),
            )
            features[key] = feature
    return list(features.values())


def parse_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    if not value:
        return None
    west, south, east, north = (float(part) for part in value.split(","))
    if (not all(math.isfinite(part) for part in (west, south, east, north))
            or not -180 <= west < east <= 180 or not -90 <= south < north <= 90):
        raise ValueError("bbox must be west,south,east,north")
    return west, south, east, north


def reclassify_existing_matches(bbox: tuple[float, float, float, float] | None) -> tuple[dict[str, dict], dict]:
    """Apply stricter eligibility without pretending to refresh old matches."""
    manifest = json.loads(MANIFEST_PATH.read_text())
    criteria = manifest.get("criteria") or {}
    expected = {
        "minimum_length_m": MIN_LENGTH_M,
        "caltrans_match_distance_m": MATCH_DISTANCE_M,
        "minimum_caltrans_match_coverage": MIN_MATCH_COVERAGE,
    }
    if any(criteria.get(key) != value for key, value in expected.items()):
        raise ValueError("Existing match thresholds differ; a full Caltrans rebuild is required")
    accepted = {}
    for candidate in load_candidates(bbox, input_dir=OUTPUT_DIR).values():
        feature = candidate["feature"]
        props = dict(feature["properties"])
        coverage = props.get("caltrans_match_coverage")
        if (not isinstance(coverage, (float, int)) or not math.isfinite(coverage)
                or not MIN_MATCH_COVERAGE <= coverage <= 1):
            raise ValueError("Existing feature has no usable Caltrans match evidence")
        detail = str(props["source_detail"])
        for suffix in ("; Caltrans All Roads public-network geometry match", "; Caltrans All Roads geometry proximity match"):
            detail = detail.removesuffix(suffix)
        props.update({
            "source_detail": f"{detail}; Caltrans All Roads geometry proximity match",
            "access_status": "not_verified", "match_evidence": "geometry_proximity_only",
        })
        result = {"type": "Feature", "properties": props, "geometry": feature["geometry"]}
        accepted[source_id(result)] = result
    return accepted, manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bbox", help="Optional pilot bbox: west,south,east,north")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--input-file", type=Path, help="Use a normalized GeoJSON FeatureCollection")
    inputs.add_argument("--input-dir", type=Path, default=INPUT_DIR, help="Directory of normalized GeoJSON tiles")
    parser.add_argument("--input-manifest", type=Path, help="JSON provenance manifest; scope is recorded without claiming complete coverage")
    parser.add_argument("--refresh-caltrans-cache", action="store_true")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=1, help="At most four concurrent Caltrans tile jobs")
    parser.add_argument("--output-root", type=Path, default=Path("."), help="Stage a complete build below this directory before publishing")
    parser.add_argument("--reclassify-existing", action="store_true", help="Filter existing matched roads, retaining their original Caltrans evidence date")
    args = parser.parse_args()
    if args.reclassify_existing and (args.input_file or args.input_dir != INPUT_DIR or args.refresh_caltrans_cache):
        parser.error("--reclassify-existing only supports the original input and stored matches")
    bbox = parse_bbox(args.bbox)
    provenance = input_provenance(args.input_file, args.input_dir, args.input_manifest)
    candidates = load_candidates(bbox, args.input_file, args.input_dir)
    if not candidates and not args.reclassify_existing:
        raise SystemExit("No eligible candidate roads; existing outputs were left in place")
    by_tile: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for candidate in candidates.values():
        lon = sum(point[0] for point in candidate["coords"]) / len(candidate["coords"])
        lat = sum(point[1] for point in candidate["coords"]) / len(candidate["coords"])
        by_tile[lonlat_to_tile(lon, lat)].append(candidate)

    verified: dict[str, dict] = {}
    candidate_count = len(candidates)
    if args.reclassify_existing:
        verified, previous_manifest = reclassify_existing_matches(bbox)
        if "input_scope" in previous_manifest:
            provenance.update({key: value for key, value in previous_manifest.items() if key.startswith("input_")})
            candidate_count = previous_manifest["candidate_count"]
        provenance.update({key: value for key, value in previous_manifest.items()
                           if key.startswith(("caltrans_geometry_cache_", "caltrans_cache_timestamp_"))})
        provenance.update({
            "caltrans_matched_utc": previous_manifest.get("caltrans_matched_utc") or previous_manifest["built_utc"],
            "verification_method": "reclassified_existing_caltrans_matches",
        })
    else:
        def verify_job(item: tuple) -> list[dict]:
            _, items = item
            return verify_tile(items, fetch_candidate_caltrans_features(items, args.refresh_caltrans_cache))

        print(f"Matching {len(candidates):,} candidates in {len(by_tile)} Caltrans tiles with {args.workers} worker(s)", flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            for index, features in enumerate(executor.map(verify_job, sorted(by_tile.items())), start=1):
                for feature in features:
                    verified[source_id(feature)] = feature
                print(f"Matched {index}/{len(by_tile)} candidate tiles; accepted {len(verified):,} roads", flush=True)
        provenance["verification_method"] = "caltrans_geometry_proximity"
        provenance.update(caltrans_cache_provenance(list(candidates.values())))

    if not verified:
        raise SystemExit("No matched candidate roads; existing outputs were left in place")
    output_dir = args.output_root / OUTPUT_DIR
    manifest_path = args.output_root / MANIFEST_PATH
    staging_path = args.output_root / STAGING_PATH
    staging_manifest_path = args.output_root / STAGING_MANIFEST_PATH
    published_manifest_path = args.output_root / PUBLISHED_MANIFEST_PATH

    tile_features: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for feature in verified.values():
        for ref in feature_refs(feature["geometry"]["coordinates"]):
            tile_features[ref].append(feature)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    for (x, y), features in tile_features.items():
        destination = output_dir / str(x)
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
            "reject_explicit_paved_contradictions": True,
            "excluded_general_access_values": sorted(BLOCKED_ACCESS_VALUES),
            "restricted_vehicle_access_requires_explicit_bicycle_or_foot_access": True,
            "reject_both_bicycle_and_foot_access_restricted": True,
            "minimum_length_m": MIN_LENGTH_M,
            "caltrans_match_distance_m": MATCH_DISTANCE_M,
            "caltrans_reference_extent": (
                (previous_manifest.get("criteria") or {}).get("caltrans_reference_extent", "Previous match extent, not reconstructed")
                if args.reclassify_existing else "All tiles intersected by each candidate geometry"
            ),
            "minimum_caltrans_match_coverage": MIN_MATCH_COVERAGE,
        },
        "built_utc": now,
        "caltrans_matched_utc": provenance.get("caltrans_matched_utc", now),
        "candidate_count": candidate_count,
        "verified_feature_count": len(all_features),
        "surface_counts": dict(sorted(surface_counts.items())),
        "scope_bbox": list(bbox) if bbox else None,
        "vector_display_simplification": "Densest road features are thinned when needed to fit coarse-zoom vector tiles; full GeoJSON candidates are retained",
        **provenance,
        "limitations": [
            "OSM surface tags are observations, not a current-surface guarantee",
            "Caltrans proximity can match a distinct parallel road and does not establish legal bicycle or foot access",
            "Candidate coverage is limited by input scope, OSM completeness, length and geometry thresholds",
        ],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({
        **common, "tile_zoom": TILE_ZOOM, "tile_count": len(tile_features),
        "tiles": {f"{TILE_ZOOM}/{x}/{y}": {"feature_count": len(features)} for (x, y), features in sorted(tile_features.items())},
    }, indent=2, sort_keys=True))
    staging_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path.write_text(json.dumps({"type": "FeatureCollection", "features": all_features}, separators=(",", ":")))
    vector_manifest = {
        **common, "layer_name": "verified_public_roads", "min_zoom": 0, "max_zoom": 14,
        "base_zoom": 10, "staged_feature_count": len(all_features),
        "staging_path": str(STAGING_PATH),
    }
    staging_manifest_path.write_text(json.dumps(vector_manifest, indent=2, sort_keys=True))
    published_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    published_manifest_path.write_text(json.dumps(vector_manifest, indent=2, sort_keys=True))
    print(json.dumps({"candidates": candidate_count, "verified": len(all_features), "tiles": len(tile_features)}, indent=2))


if __name__ == "__main__":
    main()
