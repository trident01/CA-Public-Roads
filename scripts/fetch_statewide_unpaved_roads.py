#!/usr/bin/env python3
"""Fetch explicit-surface California OSM roads without a forest-area crop.

This is source preparation, not public-access verification. The output is meant
for build_verified_public_unpaved_roads.py --input-file. Only exact unpaved
surface tags and minor road classes are retained. No surface is inferred from
highway=track. Raw Overpass responses are cached per latitude band under build/.
Successful bands are reused after interruption; partial/error responses are
never cached. Every band's OSM timestamp is recorded. An optional --snapshot
pins historical OSM data when the chosen endpoint supports archival queries.

OSM tagging references:
  https://wiki.openstreetmap.org/wiki/Key:surface
  https://wiki.openstreetmap.org/wiki/Key:access
  https://wiki.openstreetmap.org/wiki/Overpass_API
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_OUTPUT_DIR = Path("build/statewide_unpaved")
DEFAULT_ENDPOINT = "https://overpass-api.de/api/interpreter"
# Secondary/primary roads and paths are deliberately outside this road layer.
# A minor road may be unpaved regardless of its network-function highway class.
ROAD_CLASSES = frozenset({
    "track", "unclassified", "tertiary", "residential", "living_street", "service",
})
UNPAVED_SURFACES = frozenset({
    "dirt", "gravel", "ground", "unpaved", "sand", "earth", "mud", "clay",
    "grass", "fine_gravel", "pebblestone", "compacted", "cinder", "rock",
    "stone", "woodchips",
})
BLOCKED_ACCESS = frozenset({
    "private", "no", "destination", "customers", "delivery", "agricultural",
    "forestry", "permit", "permit_required", "military", "emergency",
    "employees", "restricted", "official",
})
POSITIVE_ACCESS = frozenset({"yes", "designated", "permissive", "official"})
BLOCKED_SERVICE = frozenset({"driveway", "parking_aisle", "drive-through", "emergency_access"})
ACCESS_KEYS = frozenset({"access", "vehicle", "motor_vehicle", "bicycle", "foot"})
SURFACE_KEYS = frozenset({
    "surface", "surface:grade", "surface:material", "paved", "tracktype", "smoothness",
    "source:surface", "check_date:surface", "survey:date",
})
# Latitude bands cover California's complete extent. The area predicate removes
# Nevada, Arizona, Oregon and Mexico. Returned ways retain complete geometry.
STATE_BBOX = (-124.6, 32.4, -114.0, 42.1)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    temporary.replace(path)


def latitude_bands() -> list[tuple[float, float]]:
    return [(south, min(south + 1.0, STATE_BBOX[3])) for south in (32.4 + i for i in range(10))]


def overpass_query(south: float, north: float, timeout: int, snapshot: str | None = None) -> str:
    date = f'[date:"{snapshot}"]' if snapshot else ""
    classes = "|".join(sorted(ROAD_CLASSES))
    surfaces = "|".join(sorted(UNPAVED_SURFACES))
    return (
        f"[out:json][timeout:{timeout}][maxsize:268435456]{date};\n"
        'area["ISO3166-2"="US-CA"]["admin_level"="4"]->.ca;\n'
        f'way(area.ca)({south},{STATE_BBOX[0]},{north},{STATE_BBOX[2]})'
        f'["highway"~"^({classes})$"]["surface"~"^({surfaces})$"];\n'
        "out body geom;\n"
    )


def validate_response(payload: dict) -> None:
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise ValueError("Overpass response is not an elements collection")
    # Overpass can return some elements alongside a runtime timeout remark.
    if payload.get("remark") or payload.get("error"):
        raise ValueError(f"Incomplete Overpass response: {payload.get('remark') or payload.get('error')}")
    if not (payload.get("osm3s") or {}).get("timestamp_osm_base"):
        raise ValueError("Overpass response has no OSM snapshot timestamp")


def request_overpass(endpoint: str, query: str, timeout: int, attempts: int, transport: str = "curl") -> dict:
    encoded = urllib.parse.urlencode({"data": query})
    request = urllib.request.Request(f"{endpoint}?{encoded}", headers={
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Forest-Service-Maps/1.0 (one-off California explicit-unpaved-road data build)",
    })
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            if transport == "curl":
                # System curl uses the platform HTTPS trust store on macOS;
                # standalone Python installations may have a different store.
                # Certificate verification is always enabled in both clients.
                result = subprocess.run([
                    "curl", "--silent", "--show-error", "--fail", "--get", "--connect-timeout", "20",
                    "--max-time", str(timeout + 30), "--user-agent", request.headers["User-agent"],
                    "--header", "Accept: application/json", "--data-urlencode", f"data={query}", endpoint,
                ], capture_output=True, check=False)
                if result.returncode:
                    raise OSError(result.stderr.decode(errors="replace").strip())
                payload = json.loads(result.stdout)
            else:
                with urllib.request.urlopen(request, timeout=timeout + 30) as response:
                    payload = json.load(response)
            validate_response(payload)
            return payload
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as error:
            last_error = error
            if attempt + 1 < attempts:
                delay = min(60, 30 * 2 ** attempt)
                print(f"Overpass failed: {error}. Retry in {delay}s.", flush=True)
                time.sleep(delay)
    raise RuntimeError(f"Overpass download failed after {attempts} attempts: {last_error}")


def response_timestamps(payload: dict) -> list[str]:
    metadata = payload.get("osm3s") or {}
    return metadata.get("source_timestamps") or [metadata["timestamp_osm_base"]]


def fetch_region(endpoint: str, south: float, north: float, timeout: int, attempts: int,
                 transport: str, snapshot: str | None, cache_dir: Path | None = None) -> tuple[dict, list[dict]]:
    """Split a timing-out band without accepting incomplete Overpass output."""
    query = overpass_query(south, north, timeout, snapshot)
    query_hash = hashlib.sha256(query.encode()).hexdigest()
    part_path = cache_dir / f"{query_hash}.json" if cache_dir else None
    if part_path and part_path.exists() and part_path.with_suffix(".meta.json").exists():
        payload = json.loads(part_path.read_text())
        validate_response(payload)
        source = json.loads(part_path.with_suffix(".meta.json").read_text())
        return payload, [source]
    try:
        payload = request_overpass(endpoint, query, timeout, attempts, transport)
        source = {"endpoint": endpoint, "query": query, "query_sha256": query_hash, "fetched_utc": utc_now(),
                  "south": south, "north": north, "osm_base_timestamps": response_timestamps(payload)}
        if part_path:
            atomic_json(part_path, payload)
            atomic_json(part_path.with_suffix(".meta.json"), source)
        return payload, [source]
    except RuntimeError as error:
        # Smaller regions help gateway timeouts, not permission or rate limits.
        if north - south <= 0.126 or not any(token in str(error).lower() for token in ("504", "timed out", "timeout")):
            raise
        middle = round((south + north) / 2, 6)
        print(f"Splitting timed-out band {south:g}..{north:g} at {middle:g}.", flush=True)
        left, left_sources = fetch_region(endpoint, south, middle, timeout, attempts, transport, snapshot, cache_dir)
        right, right_sources = fetch_region(endpoint, middle, north, timeout, attempts, transport, snapshot, cache_dir)
        elements = {(element.get("type"), element.get("id")): element
                    for response in (left, right) for element in response["elements"]}
        timestamps = sorted(set(response_timestamps(left) + response_timestamps(right)))
        payload = {"version": 0.6, "generator": "Forest-Service-Maps merged complete Overpass subqueries",
                   "osm3s": {"timestamp_osm_base": min(timestamps), "source_timestamps": timestamps,
                             "copyright": "OpenStreetMap contributors, ODbL"},
                   "elements": list(elements.values())}
        return payload, left_sources + right_sources


def normalize_feature(element: dict) -> tuple[dict | None, str | None]:
    if element.get("type") != "way" or not element.get("id"):
        return None, "not_way"
    tags = element.get("tags") or {}
    surface = str(tags.get("surface") or "").strip().lower()
    highway = str(tags.get("highway") or "").strip().lower()
    if surface not in UNPAVED_SURFACES:
        return None, "surface_not_explicit_unpaved"
    if highway not in ROAD_CLASSES:
        return None, "road_class"
    if str(tags.get("service") or "").lower() in BLOCKED_SERVICE:
        return None, "service_driveway_or_parking"
    # General restrictions remain a conservative exclusion even if a narrower
    # mode has an exception. A later mode-specific product can review these.
    if str(tags.get("access") or "").lower() in BLOCKED_ACCESS:
        return None, "general_access_restricted"
    foot = str(tags.get("foot") or "").lower()
    bicycle = str(tags.get("bicycle") or "").lower()
    if foot in BLOCKED_ACCESS and bicycle in BLOCKED_ACCESS:
        return None, "both_recreation_modes_restricted"
    motor_restricted = any(str(tags.get(key) or "").lower() in BLOCKED_ACCESS
                           for key in ("vehicle", "motor_vehicle"))
    if motor_restricted and not ({foot, bicycle} & POSITIVE_ACCESS):
        return None, "vehicle_restricted_without_explicit_recreation_access"
    geometry = element.get("geometry") or []
    coords = []
    for point in geometry:
        try:
            lon, lat = float(point["lon"]), float(point["lat"])
        except (TypeError, KeyError, ValueError):
            return None, "invalid_geometry"
        if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
            return None, "invalid_geometry"
        coords.append([round(lon, 7), round(lat, 7)])
    if len(coords) < 2 or len({tuple(point) for point in coords}) < 2:
        return None, "invalid_geometry"
    props = {
        "name": tags.get("name") or tags.get("official_name") or tags.get("ref") or "Unnamed unpaved road",
        "road_class": highway,
        "surface": surface,
        "source": "OpenStreetMap",
        "source_detail": f"osm way {element['id']}",
        "source_id": str(element["id"]),
        "osm_url": f"https://www.openstreetmap.org/way/{element['id']}",
    }
    for key, value in tags.items():
        root = key.split(":")[0]
        if key in SURFACE_KEYS or root in ACCESS_KEYS | {"surface"} or key in {"service", "seasonal", "opening_hours"}:
            props[key] = value
    return {"type": "Feature", "id": element["id"], "properties": props,
            "geometry": {"type": "LineString", "coordinates": coords}}, None


def build_candidates(payloads: list[dict]) -> tuple[list[dict], dict]:
    ways = {}
    for payload in payloads:
        validate_response(payload)
        for element in payload["elements"]:
            ways[(element.get("type"), element.get("id"))] = element
    rejected: Counter = Counter()
    features = []
    for _, element in sorted(ways.items(), key=lambda item: str(item[0])):
        feature, reason = normalize_feature(element)
        if feature:
            features.append(feature)
        else:
            rejected[reason] += 1
    return features, {"unique_source_way_count": len(ways), "rejected_counts": dict(sorted(rejected.items()))}


def write_band_preview(output_dir: Path, index: int, payload: dict, source: dict) -> None:
    """Make successful bands available to a matcher before the full fetch ends."""
    features, audit = build_candidates([payload])
    south, north = latitude_bands()[index]
    band_dir = output_dir / "bands" / f"band_{index:02d}"
    atomic_json(band_dir / "candidates.geojson", {"type": "FeatureCollection", "features": features})
    atomic_json(band_dir / "candidates_manifest.json", {
        "source_name": "OpenStreetMap explicit unpaved minor roads",
        "source_urls": ["https://www.openstreetmap.org/copyright", source.get("endpoint")],
        "fetched_utc": source.get("fetched_utc"),
        "osm_base_timestamps": response_timestamps(payload),
        "scope": {"kind": "partial_california_band", "description": f"California latitude {south:g}..{north:g}; whole way geometries",
                  "bbox": [STATE_BBOX[0], south, STATE_BBOX[2], north], "complete": False},
        "candidate_count": len(features), **audit,
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--transport", choices=("curl", "urllib"), default="curl")
    parser.add_argument("--snapshot", help="Optional UTC OSM historical snapshot timestamp")
    parser.add_argument("--delay", type=float, default=2)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--keep-going", action="store_true", help="Fetch other bands after a failed band; write only labelled partial combined output")
    parser.add_argument("--input", type=Path, nargs="+", help="Normalize local Overpass responses without network")
    parser.add_argument("--band", type=int, choices=range(10), action="append", help="Fetch selected bands for a partial preview")
    args = parser.parse_args()
    payloads = []
    fetched = []
    failed_bands = []
    snapshot = args.snapshot
    selected_bands = args.band or list(range(10))
    if args.input:
        for path in args.input:
            payloads.append(json.loads(path.read_text()))
            fetched.append({"path": str(path), "bytes": path.stat().st_size})
    else:
        for index in selected_bands:
            south, north = latitude_bands()[index]
            cache = args.output_dir / "raw" / f"band_{index:02d}.json"
            metadata = cache.with_suffix(".meta.json")
            if cache.exists() and metadata.exists() and not args.refresh:
                payload = json.loads(cache.read_text())
                validate_response(payload)
                saved = json.loads(metadata.read_text())
                if saved.get("road_classes") != sorted(ROAD_CLASSES) or saved.get("surfaces") != sorted(UNPAVED_SURFACES):
                    raise RuntimeError(f"Cached filter differs; use --refresh: {cache}")
                if saved.get("snapshot") != snapshot:
                    raise RuntimeError(f"Cached snapshot differs; use --refresh: {cache}")
            else:
                query = overpass_query(south, north, args.timeout, snapshot)
                print(f"Downloading California latitude band {index + 1}/10: {south:g}..{north:g}", flush=True)
                try:
                    payload, query_parts = fetch_region(args.endpoint, south, north, args.timeout, args.attempts,
                                                        args.transport, snapshot,
                                                        None if args.refresh else args.output_dir / "raw" / "parts")
                except RuntimeError as error:
                    if not args.keep_going:
                        raise
                    failed_bands.append({"band": index, "error": str(error)})
                    print(f"Band {index + 1} incomplete; proceeding to remaining bands: {error}", flush=True)
                    continue
                saved = {"endpoint": args.endpoint, "query": query,
                         "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                         "fetched_utc": utc_now(), "snapshot": snapshot,
                         "road_classes": sorted(ROAD_CLASSES), "surfaces": sorted(UNPAVED_SURFACES),
                         "query_parts": query_parts}
                atomic_json(cache, payload)
                atomic_json(metadata, saved)
                if index != selected_bands[-1]:
                    time.sleep(args.delay)
            payloads.append(payload)
            write_band_preview(args.output_dir, index, payload, saved)
            fetched.append({"band": index, "south": south, "north": north, "path": str(cache),
                            "bytes": cache.stat().st_size, "element_count": len(payload["elements"]), **saved})
            print(f"Cached {len(payload['elements']):,} ways ({cache.stat().st_size / 1e6:.1f} MB).", flush=True)
    features, audit = build_candidates(payloads)
    if not features:
        raise RuntimeError("No candidates returned; existing outputs were not replaced")
    complete = not args.input and {entry["band"] for entry in fetched} == set(range(10))
    manifest = {
        "source_name": "OpenStreetMap explicit unpaved minor roads",
        "source_urls": ["https://www.openstreetmap.org/copyright", args.endpoint],
        "fetched_utc": utc_now(),
        "osm_base_timestamps": sorted({timestamp for p in payloads for timestamp in response_timestamps(p)}),
        "requested_snapshot": snapshot,
        "scope": {"kind": "statewide_california" if complete else "partial_or_custom",
                  "description": "California OSM administrative area, without a forest crop" if complete else "Selected bands or supplied local files; coverage is incomplete",
                  "bbox": list(STATE_BBOX), "area_filter": 'ISO3166-2=US-CA; admin_level=4',
                  "complete": complete, "complete_way_geometries": True},
        "candidate_count": len(features), **audit,
        "surface_counts": dict(sorted(Counter(f["properties"]["surface"] for f in features).items())),
        "road_class_counts": dict(sorted(Counter(f["properties"]["road_class"] for f in features).items())),
        "criteria": {"explicit_unpaved_surface_only": True, "road_classes": sorted(ROAD_CLASSES),
                     "surfaces": sorted(UNPAVED_SURFACES), "blocked_general_access": sorted(BLOCKED_ACCESS),
                     "minimum_length_m": None, "public_network_verification": "Requires separate Caltrans matcher"},
        "downloads": fetched,
        "failed_bands": failed_bands,
    }
    suffix = ".partial" if failed_bands else ""
    output_path = args.output_dir / f"candidates{suffix}.geojson"
    atomic_json(output_path, {"type": "FeatureCollection", "features": features})
    atomic_json(args.output_dir / f"candidates_manifest{suffix}.json", manifest)
    print(json.dumps({"candidate_count": len(features), **audit, "scope": manifest["scope"],
                      "output_path": str(output_path)}, indent=2), flush=True)
    if failed_bands:
        parser.exit(1, "Incomplete source: successful bands cached; rerun to resume failed bands.\n")


if __name__ == "__main__":
    main()
