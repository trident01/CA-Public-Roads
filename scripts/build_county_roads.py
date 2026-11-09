#!/usr/bin/env python3
"""Download explicit maintained dirt/gravel roads from official county GIS.

Riverside is the first supported county. No OpenStreetMap matching is used.
Only the unambiguous C07/C09 maintenance codes are accepted; mixed-status,
non-county, vacated, abandoned, and merely traveled codes are excluded.
This inventory does not determine current closures or bicycle/foot access.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_URL = (
    "https://gis.countyofriverside.us/arcgis_mapping/rest/services/"
    "Transportation/04_Online_Road_Book_Mapping_v2/MapServer/1"
)
AGENCY = "Riverside County Transportation Department"
SURFACE_CODES = {"C07": "gravel", "C09": "dirt"}
CODE_LABELS = {
    "C07": "Graveled Surface Maintained",
    "C09": "Dirt Surface Maintained",
}
WHERE = "TYPE IN ('C07','C09') AND (TRAVEL_DIRECTIONS IS NULL OR TRAVEL_DIRECTIONS <> 'N')"
SOURCE_WHERE = "1=1"
FIELDS = "OBJECTID,STNAME,TYPE,RDNUMBER,TRAVEL_DIRECTIONS"


def fetch_json(url, params):
    request_url = url + "?" + urlencode(params, quote_via=quote)
    for attempt in range(3):
        try:
            request = Request(request_url, headers={"User-Agent": "Forest-Service-Maps/1.0"})
            with urlopen(request, timeout=60) as response:
                data = json.load(response)
            if "error" in data:
                raise ValueError(f"ArcGIS error: {data['error']}")
            return data
        except HTTPError as exc:
            if exc.code == 403:
                # Some county gateways reject Python's TLS/client fingerprint.
                # curl is already used by the repository's other data builders.
                result = subprocess.run(
                    ["curl", "--fail", "--silent", "--show-error", "--location",
                     "--retry", "2", "--max-time", "60", request_url],
                    check=True, capture_output=True, text=True,
                )
                data = json.loads(result.stdout)
                if "error" in data:
                    raise ValueError(f"ArcGIS error: {data['error']}")
                return data
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def validate_metadata(metadata):
    if metadata.get("geometryType") != "esriGeometryPolyline":
        raise ValueError("County layer is no longer a polyline dataset")
    fields = {field["name"]: field for field in metadata.get("fields", [])}
    if not set(FIELDS.split(",")).issubset(fields):
        raise ValueError("County layer is missing required fields")
    labels = {entry["code"]: entry["name"]
              for entry in fields["TYPE"].get("domain", {}).get("codedValues", [])}
    if any(labels.get(code) != label for code, label in CODE_LABELS.items()):
        raise ValueError("County maintenance code definitions changed; review before rebuilding")
    travel_labels = {entry["code"]: entry["name"]
                     for entry in fields["TRAVEL_DIRECTIONS"].get("domain", {}).get("codedValues", [])}
    if travel_labels.get("N") != "No Ways":
        raise ValueError("County travel direction definitions changed; review before rebuilding")


def normalize_geometry(geometry):
    if not isinstance(geometry, dict) or geometry.get("type") not in {"LineString", "MultiLineString"}:
        raise ValueError("County road must have line geometry")
    multi = geometry["type"] == "MultiLineString"
    lines = geometry.get("coordinates") if multi else [geometry.get("coordinates")]
    if not isinstance(lines, list) or not lines:
        raise ValueError("County road geometry is empty")
    result = []
    for line in lines:
        if not isinstance(line, list) or len(line) < 2:
            raise ValueError("County road line has fewer than two points")
        points = []
        for coordinate in line:
            if not isinstance(coordinate, list) or len(coordinate) < 2:
                raise ValueError("County road has an invalid coordinate")
            lon, lat = coordinate[:2]
            if (not isinstance(lon, (int, float)) or not isinstance(lat, (int, float))
                    or not math.isfinite(lon) or not math.isfinite(lat)
                    or not -125 <= lon <= -114 or not 32 <= lat <= 43):
                raise ValueError("County road coordinate is outside California / WGS84")
            point = [round(lon, 6), round(lat, 6)]
            if not points or point != points[-1]:
                points.append(point)
        if len(points) < 2:
            raise ValueError("County road line collapses to a single point")
        result.append(points)
    return {"type": geometry["type"], "coordinates": result if multi else result[0]}


def normalize_feature(feature):
    props = feature.get("properties", {})
    code = props.get("TYPE")
    if code not in SURFACE_CODES or props.get("TRAVEL_DIRECTIONS") == "N":
        return None
    object_id = props.get("OBJECTID", feature.get("id"))
    if not isinstance(object_id, int):
        raise ValueError("County feature is missing its integer OBJECTID")
    road_id = f"riverside-{object_id}"
    name = str(props.get("STNAME") or "").strip() or "Unnamed county road"
    return {
        "type": "Feature",
        "id": road_id,
        "properties": {
            "name": name,
            "surface": SURFACE_CODES[code],
            "source": "Riverside County official road book",
            "url": SOURCE_URL,
            "agency": AGENCY,
            "road_id": road_id,
            "county": "Riverside",
            "county_road_number": str(props.get("RDNUMBER") or "").strip(),
            "type_code": code,
            "maintenance": CODE_LABELS[code],
            "travel_direction": props.get("TRAVEL_DIRECTIONS"),
        },
        "geometry": normalize_geometry(feature.get("geometry")),
    }


def build(output):
    metadata = fetch_json(SOURCE_URL, {"f": "json"})
    validate_metadata(metadata)
    query = SOURCE_URL + "/query"
    # Read the county's published unpaved layer, then apply our narrower policy
    # locally so exclusions are independently testable and recorded below.
    ids_response = fetch_json(query, {"f": "json", "where": SOURCE_WHERE, "returnIdsOnly": "true"})
    ids = sorted(ids_response.get("objectIds") or [])
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("County query returned empty or duplicate object IDs")
    features = []
    seen = set()
    page_size = min(300, metadata.get("maxRecordCount", 10000))
    for offset in range(0, len(ids), page_size):
        batch_ids = ids[offset:offset + page_size]
        page = fetch_json(query, {
            "f": "geojson", "objectIds": ",".join(map(str, batch_ids)),
            "where": SOURCE_WHERE, "outFields": FIELDS, "outSR": 4326,
            "returnGeometry": "true", "returnZ": "false", "returnM": "false",
        })
        if page.get("type") != "FeatureCollection" or page.get("exceededTransferLimit"):
            raise ValueError("County geometry query was incomplete")
        batch = []
        batch_seen = set()
        for raw in page.get("features", []):
            source_id = raw.get("properties", {}).get("OBJECTID", raw.get("id"))
            if source_id in seen or source_id in batch_seen:
                raise ValueError(f"County returned duplicate OBJECTID {source_id}")
            batch_seen.add(source_id)
            feature = normalize_feature(raw)
            if feature is not None:
                batch.append(feature)
        if batch_seen != set(batch_ids):
            raise ValueError("County query omitted or added object IDs; rerun against a stable snapshot")
        seen.update(batch_seen)
        features.extend(batch)
        print(f"Read {len(seen)}/{len(ids)} county road segments; kept {len(features)}", flush=True)
    if not features:
        raise ValueError("County query contained no explicitly maintained dirt/gravel roads")
    features.sort(key=lambda feature: int(feature["id"].split("-")[-1]))
    collection = {"type": "FeatureCollection", "features": features}
    payload = (json.dumps(collection, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    coordinates = []
    for feature in features:
        geometry = feature["geometry"]
        lines = geometry["coordinates"] if geometry["type"] == "MultiLineString" else [geometry["coordinates"]]
        coordinates.extend(point for line in lines for point in line)
    bounds = [min(point[0] for point in coordinates), min(point[1] for point in coordinates),
              max(point[0] for point in coordinates), max(point[1] for point in coordinates)]
    manifest = {
        "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "agency": AGENCY,
        "county": "Riverside",
        "layer_name": metadata.get("name"),
        "source_url": SOURCE_URL,
        "source_service_item_id": metadata.get("serviceItemId"),
        "source_copyright": metadata.get("copyrightText"),
        "query_where": SOURCE_WHERE,
        "local_selection": WHERE,
        "source_feature_count": len(ids),
        "excluded_feature_count": len(ids) - len(features),
        "out_fields": FIELDS.split(","),
        "source_spatial_reference": metadata.get("sourceSpatialReference", {}).get("latestWkid"),
        "output_spatial_reference": "EPSG:4326",
        "coordinate_precision_decimals": 6,
        "surface_code_definitions": CODE_LABELS,
        "feature_count": len(features),
        "surface_counts": dict(sorted(Counter(feature["properties"]["surface"] for feature in features).items())),
        "bounds": bounds,
        "geojson": "_county_roads/riverside.geojson",
        "geojson_bytes": len(payload),
        "geojson_sha256": hashlib.sha256(payload).hexdigest(),
        "coverage": "Riverside County segments explicitly classified C07 or C09; not statewide county coverage.",
        "limitations": [
            "Road maintenance and surface inventory; not a live closure or access advisory.",
            "Bicycle and foot permission are not established by this dataset.",
            "Mixed-status, non-county, accepted-only, vacated, abandoned, and merely traveled codes are excluded.",
            "TRAVEL_DIRECTIONS=N (No Ways) is excluded; other direction codes are preserved.",
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    geojson_path = output / "riverside.geojson"
    geojson_path.write_bytes(payload)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {geojson_path}: {len(features)} features, {len(payload):,} bytes")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "_county_roads")
    args = parser.parse_args()
    try:
        build(args.output_dir)
    except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        print(f"County-road build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
