#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RAW_DIR="$ROOT_DIR/_public_roads_raw_tiles/10"
mkdir -p "$RAW_DIR"

python3 - <<'PY' "$ROOT_DIR" "$RAW_DIR"
import json
import math
import subprocess
import sys
import time
from pathlib import Path

root = Path(sys.argv[1])
raw_dir = Path(sys.argv[2])
manifest = json.loads((root / "roads_tiles_manifest.json").read_text())
tile_keys = sorted(manifest.get("tiles", {}).keys())
endpoints = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


def expand_tile_keys(keys, buffer=1):
    """Add a ring of buffer tiles around each forest tile to cover
    roads just outside national forest boundaries."""
    expanded = set()
    for key in keys:
        zoom_str, x_str, y_str = key.split("/")
        zoom = int(zoom_str)
        x = int(x_str)
        y = int(y_str)
        for dx in range(-buffer, buffer + 1):
            for dy in range(-buffer, buffer + 1):
                tx = x + dx
                ty = y + dy
                max_tile = (2 ** zoom) - 1
                if 0 <= tx <= max_tile and 0 <= ty <= max_tile:
                    expanded.add(f"{zoom}/{tx}/{ty}")
    return sorted(expanded)


tile_keys = expand_tile_keys(tile_keys, buffer=1)


def tile_to_lon(x, z):
    return x / (2 ** z) * 360.0 - 180.0


def tile_to_lat(y, z):
    n = math.pi - 2.0 * math.pi * y / (2 ** z)
    return math.degrees(math.atan(math.sinh(n)))


for tile_key in tile_keys:
    zoom_str, x_str, y_str = tile_key.split("/")
    zoom = int(zoom_str)
    x = int(x_str)
    y = int(y_str)

    west = tile_to_lon(x, zoom)
    east = tile_to_lon(x + 1, zoom)
    north = tile_to_lat(y, zoom)
    south = tile_to_lat(y + 1, zoom)

    # Included road types:
    #   track        – dirt/gravel forest & farm roads
    #   service      – short access roads (keep alleys, exclude parking aisles)
    #   unclassified – minor rural roads (often unpaved in the west)
    #   residential  – neighbourhood roads (many unpaved in remote areas)
    #   road         – catch-all for roads of unknown classification
    #
    # Surface filter: include if NO surface tag at all (uncertain / likely
    # unpaved in rural areas), OR if the surface tag matches known unpaved
    # materials.  Explicitly paved roads (asphalt, concrete, paving_stones,
    # chipseal, bricks, etc.) are excluded because they won't match.
    query = f'''[out:json][timeout:45];
(
  way["highway"~"track|service|unclassified|residential|road"]
    ["access"!~"private|no|destination"]
    ["motor_vehicle"!~"private|no|destination"]
    ["service"!~"parking_aisle|driveway"]
    (if: !exists(t["surface"]) || t["surface"] ~ "^(dirt|gravel|ground|unpaved|sand|earth|mud|clay|grass|fine_gravel|pebblestone|compacted|cinder|rock|stone|woodchips)$")
    ({south:.6f},{west:.6f},{north:.6f},{east:.6f});
);
out geom;'''

    out_dir = raw_dir / x_str
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{y_str}.json"
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"Skipping {tile_key} (already fetched)", flush=True)
        continue

    print(f"Fetching {tile_key}", flush=True)
    last_error = None
    for attempt in range(6):
        endpoint = endpoints[attempt % len(endpoints)]
        try:
            subprocess.run([
                "curl",
                "-sS",
                "--fail",
                "--retry", "2",
                "--retry-delay", "3",
                "--max-time", "120",
                "-X", "POST",
                endpoint,
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
            print(f"Retrying {tile_key} in {wait_seconds}s after {endpoint} failed", flush=True)
            time.sleep(wait_seconds)
    if last_error:
        raise last_error

    time.sleep(1.0)
PY
