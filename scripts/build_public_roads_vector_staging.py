#!/usr/bin/env python3
"""
Build a combined staging GeoJSON for supplemental public roads so Tippecanoe
can generate a dedicated PMTiles source for vector mode.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

SOURCE_DIR = Path("_public_roads_raw_tiles") / "10"
BOUNDARIES_PATH = Path("_public_lands") / "ca_forest_boundaries.geojson"
OUTPUT_DIR = Path("build") / "vector_tiles"
PUBLISH_DIR = Path("vector_tiles")
STAGING_FILENAME = "public_roads_staging.geojson"
MANIFEST_FILENAME = "public_roads_staging_manifest.json"
MBTILES_FILENAME = "public_roads.mbtiles"
PMTILES_FILENAME = "public_roads.pmtiles"
TIPPECANOE_LAYER = "public_roads"
TIPPECANOE_MIN_ZOOM = 0
TIPPECANOE_MAX_ZOOM = 13
TIPPECANOE_BASE_ZOOM = 10
FOREST_BBOX_PAD_DEGREES = 0.1
# Extra regions to always include (not filtered by forest adjacency).
# [west, south, east, north]  (lon/lat decimal degrees)
EXTRA_REGION_BBOXES = [
    # SF Bay Area — peninsula, SF, east bay, south bay, north bay
    [-122.8, 37.0, -121.5, 38.6],
]
# Road classes allowed everywhere (forest-adjacent regions)
ALLOWED_ROAD_CLASSES = frozenset({
    "road",
    "service",
    "services",
    "track",
    "unclassified",
})
# Road classes additionally allowed in extra regions (e.g. urban Bay Area)
EXTRA_REGION_EXTRA_CLASSES = frozenset({
    "residential",
})
TIPPECANOE_FLAGS = (
    "--no-tile-compression",
    f"--layer={TIPPECANOE_LAYER}",
    f"--minimum-zoom={TIPPECANOE_MIN_ZOOM}",
    f"--maximum-zoom={TIPPECANOE_MAX_ZOOM}",
    f"--base-zoom={TIPPECANOE_BASE_ZOOM}",
    "--drop-densest-as-needed",
    "--extend-zooms-if-still-dropping",
)
ALLOWED = frozenset({
    "name",
    "road_class",
    "surface",
    "tracktype",
    "motor_vehicle",
    "access",
    "source",
    "source_detail",
    "surface_inferred",
})


def normalize_feature(element: dict, source_tile: str) -> dict | None:
    if element.get("type") != "way":
        return None

    geometry = element.get("geometry") or []
    if len(geometry) < 2:
        return None

    tags = element.get("tags") or {}
    props = {
        "name": (
            tags.get("name")
            or tags.get("official_name")
            or tags.get("ref")
            or tags.get("unsigned_ref")
            or "(unnamed public road)"
        ),
        "road_class": tags.get("highway") or "",
        "surface": tags.get("surface") or "",
        "tracktype": tags.get("tracktype") or "",
        "motor_vehicle": tags.get("motor_vehicle") or "",
        "access": tags.get("access") or "",
        "source": "OpenStreetMap",
        "source_detail": f"osm way {element['id']}",
        "source_tile": source_tile,
    }
    if not tags.get("surface"):
        props["surface_inferred"] = True

    road_class = props.get("road_class", "")
    tracktype = props.get("tracktype", "")
    if props.get("surface_inferred") and (
        road_class in ("residential", "service", "road", "unclassified")
        or tracktype == "grade1"
    ):
        return None

    return {
        "type": "Feature",
        "id": element["id"],
        "properties": {key: value for key, value in props.items() if value not in ("", None)},
        "geometry": {
            "type": "LineString",
            "coordinates": [
                [round(float(point["lon"]), 5), round(float(point["lat"]), 5)]
                for point in geometry
            ],
        },
    }


def tippecanoe_command(staging_path: Path, mbtiles_path: Path) -> str:
    args = list(TIPPECANOE_FLAGS) + ["-o", str(mbtiles_path), str(staging_path)]
    return "tippecanoe " + " ".join(args)


def extract_coords(geom: dict) -> list[tuple[float, float]]:
    geom_type = geom.get("type")
    coords = geom.get("coordinates") or []
    if geom_type == "LineString":
        return [(coord[0], coord[1]) for coord in coords]
    if geom_type == "MultiLineString":
        return [(coord[0], coord[1]) for line in coords for coord in line]
    return []


def extract_all_coords(geom: dict) -> list[tuple[float, float]]:
    coords: list[tuple[float, float]] = []

    def walk(value: object) -> None:
        if not isinstance(value, list) or not value:
            return
        first = value[0]
        if isinstance(first, (int, float)) and len(value) >= 2:
            coords.append((float(value[0]), float(value[1])))
            return
        for item in value:
            walk(item)

    walk(geom.get("coordinates") or [])
    return coords


def load_forest_bboxes() -> list[list[float]]:
    if not BOUNDARIES_PATH.exists():
        print(f"ERROR: forest boundaries file not found: {BOUNDARIES_PATH}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(BOUNDARIES_PATH.read_text())
    bboxes: list[list[float]] = []
    for feature in data.get("features") or []:
        coords = extract_all_coords(feature.get("geometry") or {})
        if not coords:
            continue
        lons = [coord[0] for coord in coords]
        lats = [coord[1] for coord in coords]
        bboxes.append([
            min(lons) - FOREST_BBOX_PAD_DEGREES,
            min(lats) - FOREST_BBOX_PAD_DEGREES,
            max(lons) + FOREST_BBOX_PAD_DEGREES,
            max(lats) + FOREST_BBOX_PAD_DEGREES,
        ])
    return bboxes


def bbox_intersects(a: tuple[float, float, float, float], b: list[float]) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def feature_bbox(feature: dict) -> tuple[float, float, float, float] | None:
    coords = extract_coords(feature.get("geometry") or {})
    if not coords:
        return None
    lons = [coord[0] for coord in coords]
    lats = [coord[1] for coord in coords]
    return (min(lons), min(lats), max(lons), max(lats))


def main() -> None:
    files = sorted(SOURCE_DIR.rglob("*.json"))
    if not files:
        print(f"ERROR: no raw public-road JSON files found in {SOURCE_DIR}", file=sys.stderr)
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    forest_bboxes = load_forest_bboxes()

    ways_by_id: dict[int, dict] = {}
    source_counts: dict[str, int] = {}
    total_normalized = 0
    forest_region_count = 0
    extra_region_count = 0
    class_filtered_out = 0

    for path in files:
        source_tile = path.relative_to(SOURCE_DIR).with_suffix("").as_posix()
        data = json.loads(path.read_text())
        elements = data.get("elements") or []
        source_counts[source_tile] = len(elements)
        for element in elements:
            feature = normalize_feature(element, source_tile)
            if feature is not None:
                total_normalized += 1
                bbox = feature_bbox(feature)
                if bbox is None:
                    continue
                in_forest = any(bbox_intersects(bbox, fb) for fb in forest_bboxes)
                in_extra = any(bbox_intersects(bbox, eb) for eb in EXTRA_REGION_BBOXES)
                if not in_forest and not in_extra:
                    continue
                road_class = (feature.get("properties") or {}).get("road_class", "")
                allowed = set(ALLOWED_ROAD_CLASSES)
                if in_extra:
                    allowed |= EXTRA_REGION_EXTRA_CLASSES
                if road_class not in allowed:
                    class_filtered_out += 1
                    continue
                if in_forest:
                    forest_region_count += 1
                if in_extra and not in_forest:
                    extra_region_count += 1
                ways_by_id[element["id"]] = feature

    all_features = list(ways_by_id.values())
    staging_path = OUTPUT_DIR / STAGING_FILENAME
    staging_path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": all_features,
    }, separators=(",", ":")))
    print(f"Wrote {len(all_features)} public-road features -> {staging_path}")

    manifest_path = OUTPUT_DIR / MANIFEST_FILENAME
    mbtiles_path = OUTPUT_DIR / MBTILES_FILENAME
    manifest = {
        "description": "Staging file for public-road vector tile build pipeline",
        "source_tile_zoom": 10,
        "source_query_file_count": len(files),
        "source_way_count": total_normalized,
        "forest_bbox_count": len(forest_bboxes),
        "forest_bbox_pad_degrees": FOREST_BBOX_PAD_DEGREES,
        "extra_region_bboxes": EXTRA_REGION_BBOXES,
        "extra_region_feature_count": extra_region_count,
        "forest_region_feature_count": forest_region_count,
        "allowed_road_classes": sorted(ALLOWED_ROAD_CLASSES),
        "filtered_out_feature_count": total_normalized - len(all_features),
        "class_filtered_out_count": class_filtered_out,
        "source_counts": source_counts,
        "allowlisted_properties": sorted(ALLOWED),
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "layer_name": TIPPECANOE_LAYER,
        "min_zoom": TIPPECANOE_MIN_ZOOM,
        "max_zoom": TIPPECANOE_MAX_ZOOM,
        "base_zoom": TIPPECANOE_BASE_ZOOM,
        "tippecanoe_flags": list(TIPPECANOE_FLAGS),
        "staged_feature_count": len(all_features),
        "staging_path": str(staging_path),
        "manifest_path": str(manifest_path),
        "mbtiles_path": str(mbtiles_path),
        "pmtiles_path": str(OUTPUT_DIR / PMTILES_FILENAME),
        "tippecanoe_command": tippecanoe_command(staging_path, mbtiles_path),
    }

    lons: list[float] = []
    lats: list[float] = []
    for feature in all_features:
        for lon, lat in extract_coords(feature.get("geometry") or {}):
            lons.append(lon)
            lats.append(lat)
    if lons and lats:
        manifest["bounds"] = [min(lons), min(lats), max(lons), max(lats)]

    manifest_json = json.dumps(manifest, indent=2, sort_keys=True)
    manifest_path.write_text(manifest_json)
    (PUBLISH_DIR / MANIFEST_FILENAME).write_text(manifest_json)
    print(f"Wrote manifest -> {manifest_path}")
    print(f"Wrote published manifest -> {PUBLISH_DIR / MANIFEST_FILENAME}")


if __name__ == "__main__":
    main()
