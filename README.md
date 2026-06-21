# CA Forest Service Roads

Interactive California forest-road map with:

- Forest Service MVUM/NFSR road overlays
- linked PDF MVUM sheets
- place search
- supplemental public-road overlay from OpenStreetMap when zoomed in
- static road tiles for faster pan/zoom on GitHub Pages
- static public-road tiles covering the full forest road footprint

## Local Development

Run:

```bash
./serve.sh
```

`./serve.sh` now starts a small local server with HTTP Range support, which is
required for PMTiles-backed vector rendering to work well locally.

Then open:

```text
http://127.0.0.1:8080
```

The main map now has a renderer switch in the top-right panel:
- `Classic GeoJSON` keeps the existing Leaflet road overlay
- `Vector Preview` attempts to use the experimental PMTiles path in the main UI

If vector tiles are not built yet, the page falls back safely to classic mode.
For the public site, the committed files in `vector_tiles/` are what make the
experimental vector renderer available.

## GitHub Pages

This repo includes a Pages workflow at `.github/workflows/deploy-pages.yml`.

To publish:

1. Create a GitHub repository and push this project to the `main` branch.
2. In GitHub, open `Settings` -> `Pages`.
3. Set `Source` to `GitHub Actions`.
4. Push to `main` or run the `Deploy GitHub Pages` workflow manually.

The site will publish all files in this repository as a static site, including:

- `index.html`
- `_roads_tiles/`
- `_roads_geojson/`
- forest PDF folders
- `vendor/`

## Road Tile Build

The interactive road overlay now uses pre-generated road tiles from `_roads_tiles/` plus `roads_tiles_manifest.json`.

To rebuild them from the source GeoJSON:

```bash
python3 scripts/generate_road_tiles.py
```

## Public Road Tile Build

The brown supplemental-road overlay uses pre-generated tiles from `_public_roads_tiles/` plus `public_roads_tiles_manifest.json`.
The cache covers the forest road tile footprint plus a 1-tile buffer outside national forest boundaries (no live Overpass fallback).

### What's included

The Overpass query fetches OSM ways tagged with any of:
- `highway=track` — forest / farm roads
- `highway=service` — short access roads (parking aisles and driveways are excluded)
- `highway=unclassified` — minor rural roads (often unpaved in the West)
- `highway=residential` — neighbourhood roads (many unpaved in remote areas)
- `highway=road` — roads of unknown classification

A way is included if its surface tag matches known unpaved materials (`dirt`, `gravel`, `ground`, `unpaved`, `sand`, `earth`, etc.) **or** if it has no surface tag at all (uncertain surface — likely unpaved in rural areas). Explicitly paved roads (asphalt, concrete, paving_stones, etc.) are excluded.

### Styling

| Legend | Style | Meaning |
|--------|-------|---------|
| Solid orange | `───` | Confirmed unpaved (explicit surface tag) |
| Dotted orange | `·· ··` | Uncertain surface (no surface tag — could be paved or unpaved) |

### To refresh the local OSM public-road cache

The raw Overpass data is gitignored. To re-fetch with the latest data or after changing the query:

```bash
# 1. Clear old raw data (or skip to resume a partial fetch)
rm -rf _public_roads_raw_tiles/10

# 2. Fetch fresh data from Overpass API (can take 15-60 minutes)
bash scripts/fetch_public_roads_raw.sh

# 3. Build output tiles and manifest
python3 scripts/build_public_roads_tiles.py
```

## Vector Tile Build (Experimental)

This is the experimental vector-tile pipeline for replacing the large GeoJSON road
overlay with lightweight vector tiles (`.pbf` via Tippecanoe / MBTiles / PMTiles).

Today there are two ways to use it locally:
- `vector_preview.html` is a dedicated MapLibre preview page
- `index.html?mode=vector` enables the main map's vector renderer

The classic Leaflet/GeoJSON renderer is still the more complete path for
popups and forest toggles, but vector mode now expects two PMTiles sources:
- `forest_roads.pmtiles` for MVUM forest roads
- `public_roads.pmtiles` for the brown supplemental OSM/public roads

### Fast start

If you already have Tippecanoe and the PMTiles CLI installed:

```bash
# 1. Build everything (staging + MBTiles + PMTiles)
bash scripts/build_vector_preview_tiles.sh

# 2. Verify outputs
python3 scripts/check_vector_preview_outputs.py

# 3. Serve and view
./serve.sh
# Open http://127.0.0.1:8080/vector_preview.html
```

**Expected artifacts** after a successful build:

| File | Description |
|------|-------------|
| `build/vector_tiles/forest_roads_staging.geojson` | Combined, property-stripped MVUM GeoJSON |
| `build/vector_tiles/forest_roads_staging_manifest.json` | MVUM metadata: counts, bounds, zoom, layer name |
| `build/vector_tiles/forest_roads.mbtiles` | MVUM MBTiles |
| `build/vector_tiles/forest_roads.pmtiles` | MVUM PMTiles |
| `build/vector_tiles/public_roads_staging.geojson` | Combined statewide public-road GeoJSON |
| `build/vector_tiles/public_roads_staging_manifest.json` | Public-road metadata: counts, bounds, zoom, layer name |
| `build/vector_tiles/public_roads.mbtiles` | Public-road MBTiles |
| `build/vector_tiles/public_roads.pmtiles` | Public-road PMTiles |
| `vector_tiles/forest_roads_staging_manifest.json` | Published MVUM manifest copy |
| `vector_tiles/forest_roads.pmtiles` | Published MVUM PMTiles copy |
| `vector_tiles/public_roads_staging_manifest.json` | Published public-road manifest copy |
| `vector_tiles/public_roads.pmtiles` | Published public-road PMTiles copy |

### Stage 1: Combine source data

[`scripts/build_vector_tile_staging.py`](scripts/build_vector_tile_staging.py) reads every
`_roads_geojson/*.geojson`, strips properties down to the set needed for styling and popups
(`forest_id`, `name`, `symbol`, `surfacetype`, `seasonal`, `system`, `districtname`), and
writes a single staging file:

```bash
python3 scripts/build_vector_tile_staging.py
```

Output:
- `build/vector_tiles/forest_roads_staging.geojson` — combined FeatureCollection
- `build/vector_tiles/forest_roads_staging_manifest.json` — source/staged counts, bounds, layer name, zoom settings, and canonical Tippecanoe command
- `vector_tiles/forest_roads_staging_manifest.json` — published manifest copy for the site

[`scripts/build_public_roads_vector_staging.py`](scripts/build_public_roads_vector_staging.py)
does the same for `_public_roads_raw_tiles/10/*.json`, deduping by OSM way id and
writing:

- `build/vector_tiles/public_roads_staging.geojson`
- `build/vector_tiles/public_roads_staging_manifest.json`
- `vector_tiles/public_roads_staging_manifest.json`

### Stage 2: Generate vector tiles (requires Tippecanoe)

**Prerequisite:** Install [Tippecanoe](https://github.com/felt/tippecanoe).

```bash
# macOS
brew install tippecanoe

# Linux
sudo apt install tippecanoe
```

Then run the build wrapper (or the `tippecanoe` command directly):

```bash
bash scripts/tippecanoe_build.sh
bash scripts/tippecanoe_build_public_roads.sh
```

The command inside (also written to the manifest as `tippecanoe_command`) is:

```bash
tippecanoe \
  --no-tile-compression \
  --layer=forest_roads \
  --minimum-zoom=0 \
  --maximum-zoom=14 \
  --base-zoom=10 \
  --drop-densest-as-needed \
  --extend-zooms-if-still-dropping \
  -o build/vector_tiles/forest_roads.mbtiles \
  build/vector_tiles/forest_roads_staging.geojson
```

Outputs:
- `build/vector_tiles/forest_roads.mbtiles`
- `build/vector_tiles/public_roads.mbtiles`

The manifest also records:
- layer name: `forest_roads`
- zoom range: `0` to `14`
- base zoom: `10`
- future output paths for both `MBTiles` and `PMTiles`

### Stage 3: Convert MBTiles to PMTiles for browser use

The browser-based vector renderers can load `PMTiles` directly, but they cannot
load raw `MBTiles` directly.

Install the CLI once:

```bash
pip install pmtiles
```

Then convert:

```bash
bash scripts/pmtiles_convert.sh
bash scripts/pmtiles_convert_public_roads.sh
```

Output:
- `build/vector_tiles/forest_roads.pmtiles`
- `vector_tiles/forest_roads.pmtiles` — published site copy
- `build/vector_tiles/public_roads.pmtiles`
- `vector_tiles/public_roads.pmtiles` — published site copy

### One-command preview build

Once `tippecanoe` and the `pmtiles` CLI are installed, you can run the whole
preview pipeline in one shot:

```bash
bash scripts/build_vector_preview_tiles.sh
```

That wrapper runs:
1. `python3 scripts/build_vector_tile_staging.py`
2. `bash scripts/tippecanoe_build.sh`
3. `bash scripts/pmtiles_convert.sh`

After step 3, the browser-facing files are synced into `vector_tiles/` so they
can be committed and served by GitHub Pages.

### Stage 4: Preview the vector tiles locally

[`vector_preview.html`](vector_preview.html) is an experimental MapLibre GL JS viewer
that renders forest roads from the vector-tile pipeline.

To use it:

1. Install Tippecanoe and the PMTiles CLI.
2. Run:
   ```bash
   bash scripts/build_vector_preview_tiles.sh
   ```
3. Serve the repo locally (`./serve.sh`) and open:
   ```
   http://127.0.0.1:8080/vector_preview.html
   ```

The preview page auto-detects whether the PMTiles file exists. If it does not,
a notice displays the exact build commands.

You can also test the same PMTiles build inside the main app:

```text
http://127.0.0.1:8080/index.html?mode=vector
```

Or use the `Renderer` select box in the top-right panel and switch to
`Vector Preview`.

**Known limitation:** MBTiles cannot be loaded directly by a browser without a
tile server (e.g., [Martin](https://github.com/maplibre/martin) or
[tileserver-gl](https://github.com/maplibre/tileserver-gl)). PMTiles conversion
is currently the simplest way to preview locally.

### Preview troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Notice says "PMTiles: missing" | `forest_roads.pmtiles` not yet built | Run `bash scripts/pmtiles_convert.sh` |
| Notice says "MBTiles exists — convert needed" | MBTiles built but PMTiles not | `pip install pmtiles && bash scripts/pmtiles_convert.sh` |
| Notice shows full build sequence | Neither MBTiles nor PMTiles exist | Install Tippecanoe + PMTiles CLI, then run `bash scripts/build_vector_preview_tiles.sh` |
| Page loads, basemap visible, but no roads at any zoom | PMTiles exists but the source/layer wiring is wrong | Check the browser console for vector-source errors, confirm `forest_roads.pmtiles` exists, and rerun `python3 scripts/check_vector_preview_outputs.py` |
| Vector mode loads very slowly or appears sparse locally | Local server does not support HTTP Range requests | Start the repo with `./serve.sh` rather than `python3 -m http.server` |
| Tippecanoe not found when running build | Tippecanoe not installed | `brew install tippecanoe` (macOS) or `sudo apt install tippecanoe` (Linux) |
| MapLibre CDN failed to load | No internet / CDN blocked | Open browser devtools Network tab and confirm `maplibre-gl` and `pmtiles` scripts loaded. If offline, vendor the files into `vendor/maplibre/` |

The status panel in the bottom-right corner of the preview page shows the
detected state of the manifest, PMTiles, and MBTiles files at load time.

### Next steps

- Improve vector-mode interactivity and popup behavior in the main app
- Decide whether the supplemental public-road overlay should stay classic-only or also move to vector tiles
- Tune low-zoom styling/generalization now that PMTiles loading is working locally
