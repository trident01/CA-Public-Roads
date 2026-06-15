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

    query = f'''[out:json][timeout:35];
(
  way["highway"~"track|service|unclassified"]
    ["access"!~"private|no|destination"]
    ["motor_vehicle"!~"private|no|destination"]
    ["service"!~"parking_aisle|driveway"]
    ["surface"~"dirt|gravel|ground|unpaved|sand|earth|mud|clay|grass|fine_gravel|pebblestone"]
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
                "--max-time", "90",
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
