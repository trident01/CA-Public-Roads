#!/usr/bin/env bash
# Fetch raw OSM data for Bay Area tiles that weren't covered by the
# forest-buffer expansion.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RAW_DIR="$ROOT_DIR/_public_roads_raw_tiles/10"
mkdir -p "$RAW_DIR"

python3 - <<'PY' "$RAW_DIR"
import json
import math
import subprocess
import sys
import time
from pathlib import Path

raw_dir = Path(sys.argv[1])

# Bay Area tiles at zoom 10 that are not covered by forest-buffer expansion
TILES = [
    (162, 393), (162, 394), (162, 395), (162, 396), (162, 397), (162, 398),
    (163, 393), (163, 394), (163, 395), (163, 396), (163, 397), (163, 398),
    (164, 393), (164, 394), (164, 395), (164, 396), (164, 397), (164, 398),
    (165, 393), (165, 394), (165, 395), (165, 396), (165, 397), (165, 398),
    (166, 393), (166, 394), (166, 395), (166, 396), (166, 397), (166, 398),
]

ZOOM = 10
endpoints = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


def tile_to_lon(x, z):
    return x / (2 ** z) * 360.0 - 180.0


def tile_to_lat(y, z):
    n = math.pi - 2.0 * math.pi * y / (2 ** z)
    return math.degrees(math.atan(math.sinh(n)))


query_template = '''[out:json][timeout:45];
(
  way["highway"~"track|service|unclassified|residential|road"]
    ["access"!~"private|no|destination"]
    ["motor_vehicle"!~"private|no|destination"]
    ["service"!~"parking_aisle|driveway"]
    ["surface"~"dirt|gravel|ground|unpaved|sand|earth|mud|clay|grass|fine_gravel|pebblestone|compacted|cinder|rock|stone|woodchips"]
    ({south:.6f},{west:.6f},{north:.6f},{east:.6f});
  way["highway"~"track|service|unclassified|residential|road"]
    ["access"!~"private|no|destination"]
    ["motor_vehicle"!~"private|no|destination"]
    ["service"!~"parking_aisle|driveway"]
    [!"surface"]
    ({south:.6f},{west:.6f},{north:.6f},{east:.6f});
);
out geom;'''

for x, y in TILES:
    west = tile_to_lon(x, ZOOM)
    east = tile_to_lon(x + 1, ZOOM)
    north = tile_to_lat(y, ZOOM)
    south = tile_to_lat(y + 1, ZOOM)

    query = query_template.format(south=south, west=west, north=north, east=east)

    out_dir = raw_dir / str(x)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{y}.json"
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"Skipping {ZOOM}/{x}/{y} (already fetched)", flush=True)
        continue

    print(f"Fetching {ZOOM}/{x}/{y}", flush=True)
    last_error = None
    for attempt in range(6):
        endpoint = endpoints[attempt % len(endpoints)]
        try:
            subprocess.run([
                "curl", "-sS", "--fail",
                "--retry", "2", "--retry-delay", "3",
                "--max-time", "120",
                "-X", "POST", endpoint,
                "-H", "User-Agent: CA-Public-Roads tile builder",
                "-H", "Accept: application/json",
                "-H", "Content-Type: application/x-www-form-urlencoded;charset=UTF-8",
                "--data-urlencode", f"data={query}",
                "-o", str(out_path),
            ], check=True)
            last_error = None
            break
        except subprocess.CalledProcessError as error:
            last_error = error
            wait_seconds = min(20, 3 + attempt * 3)
            print(f"Retrying in {wait_seconds}s after {endpoint} failed", flush=True)
            time.sleep(wait_seconds)
    if last_error:
        raise last_error

    time.sleep(1.0)

print("Done fetching Bay Area tiles!", flush=True)
PY
