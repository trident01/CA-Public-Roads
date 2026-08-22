# CA Forest Service Roads

Interactive California forest-road map with:

- Forest Service MVUM/NFSR road overlays
- opt-in official BLM public-motorized unpaved roads
- linked PDF MVUM sheets
- place search
- static road tiles for faster pan/zoom on GitHub Pages

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
For the public site, the committed Forest Service and BLM files in
`vector_tiles/` make the vector renderer available.

## GitHub Pages

This repo includes a Pages workflow at `.github/workflows/deploy-pages.yml`.

To publish:

1. Create a GitHub repository and push this project to the `main` branch.
2. In GitHub, open `Settings` -> `Pages`.
3. Set `Source` to `GitHub Actions`.
4. Push to `main` or run the `Deploy GitHub Pages` workflow manually.

The site publishes only the runtime assets selected by the Pages workflow:

- `index.html`
- `_roads_tiles/`
- `roads_tiles_manifest.json`
- `_blm_roads_tiles/` and `blm_public_roads_tiles_manifest.json`
- the Forest Service and BLM PMTiles sources and manifests
- `vendor/`

## Road Tile Build

The interactive road overlay now uses pre-generated road tiles from `_roads_tiles/` plus `roads_tiles_manifest.json`.

To rebuild them from the source GeoJSON:

```bash
python3 scripts/generate_road_tiles.py
```

## Official BLM Public Roads

The optional brown dashed layer comes from layer 0 of BLM's public-display
Ground Transportation Linear Features (GTLF) service. It is off by default and
only renders at zoom 8 or closer. The build applies the same conservative rules
both in the server query and in a local validator:

- California records only
- BLM route-designation authority
- route designation `Open` and planned mode `Motorized`
- observed surface is `Natural`, `Natural Improved`, or `Aggregate`
- observed route-use class supports a full-size vehicle (`2WD Low`, `4WD Low`,
  or `4WD High Clearance / Specialized`)
- excludes administrative, permit-only, and all-access-restricted records

The current build contains 4,216 segments covering 2,613.2 GIS miles. These
are much stronger access signals than OSM tags, but they are not a live closure
feed; the UI tells users to verify temporary and local closures.

Rebuild the classic tiles and vector staging data from the official service:

```bash
python3 scripts/build_blm_public_roads.py
bash scripts/tippecanoe_build_blm_roads.sh
bash scripts/pmtiles_convert_blm_roads.sh
```

Source: [BLM National GTLF Public Display, public motorized roads](https://gis.blm.gov/arcgis/rest/services/transportation/BLM_Natl_GTLF_Public_Display/MapServer/0)

## Experimental OSM Supplemental Roads (Disabled)

The OpenStreetMap-derived brown road overlay is disabled in `index.html` and
excluded from the GitHub Pages artifact. The existing source data and build
scripts remain in the repository only for offline analysis.

The July 2026 dataset is not trustworthy enough to imply useful or legal public
motor-vehicle access:

| Audit result | Ways | Share |
|---|---:|---:|
| Total included ways | 137,182 | 100% |
| No explicit surface tag | 103,430 | 75.4% |
| Unnamed | 106,730 | 77.8% |
| No `access` tag | 135,373 | 98.7% |
| No `motor_vehicle` tag | 135,061 | 98.5% |

The old filter treated a missing restriction as evidence that a road was public
and motorable. It also used padded forest bounding boxes rather than actual land
ownership or road jurisdiction, and it rendered the layer at every zoom. Surface,
road class, and proximity to a forest cannot establish legal access.

A future OSM experiment should be opt-in and separately labeled. At minimum it
should require an explicit unpaved surface plus an explicit positive access tag
(`access` or `motor_vehicle` equal to `yes`, `permissive`, or `designated`), use
actual forest polygons, and be validated against authoritative agency data. Only
2,350 current ways pass even that preliminary filter, and those still require
validation before publication.

## Vector Tile Build (Experimental)

This is the experimental vector-tile pipeline for replacing the large GeoJSON road
overlay with lightweight vector tiles (`.pbf` via Tippecanoe / MBTiles / PMTiles).

Today there are two ways to use it locally:
- `vector_preview.html` is a dedicated MapLibre preview page
- `index.html?mode=vector` enables the main map's vector renderer

The classic Leaflet/GeoJSON renderer is still the more complete path for forest
toggles. Both renderers support the opt-in official BLM layer. The OSM
`public_roads.pmtiles` source is retained solely for local experiments in
`vector_preview.html`; it is disabled in `index.html` and is not deployed.

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
| `build/vector_tiles/blm_public_roads_staging.geojson` | Validated official BLM public-road GeoJSON |
| `build/vector_tiles/blm_public_roads_staging_manifest.json` | BLM metadata: query, counts, bounds, zoom, layer name |
| `build/vector_tiles/blm_public_roads.mbtiles` | BLM-road MBTiles |
| `build/vector_tiles/blm_public_roads.pmtiles` | BLM-road PMTiles |
| `vector_tiles/forest_roads_staging_manifest.json` | Published MVUM manifest copy |
| `vector_tiles/forest_roads.pmtiles` | Published MVUM PMTiles copy |
| `vector_tiles/blm_public_roads_staging_manifest.json` | Published BLM manifest copy |
| `vector_tiles/blm_public_roads.pmtiles` | Published BLM PMTiles copy |

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

[`scripts/build_blm_public_roads.py`](scripts/build_blm_public_roads.py) fetches
and independently validates the official BLM records, then writes classic tiles
and vector staging:

- `_blm_roads_tiles/10/*.geojson`
- `blm_public_roads_tiles_manifest.json`
- `build/vector_tiles/blm_public_roads_staging.geojson`
- `build/vector_tiles/blm_public_roads_staging_manifest.json`
- `vector_tiles/blm_public_roads_staging_manifest.json`

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
bash scripts/tippecanoe_build_blm_roads.sh
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
- `build/vector_tiles/blm_public_roads.mbtiles`

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
bash scripts/pmtiles_convert_blm_roads.sh
```

Output:
- `build/vector_tiles/forest_roads.pmtiles`
- `vector_tiles/forest_roads.pmtiles` — published site copy
- `build/vector_tiles/blm_public_roads.pmtiles`
- `vector_tiles/blm_public_roads.pmtiles` — published site copy

### One-command preview build

Once `tippecanoe` and the `pmtiles` CLI are installed, you can run the whole
preview pipeline in one shot:

```bash
bash scripts/build_vector_preview_tiles.sh
```

That wrapper runs:
1. `python3 scripts/build_vector_tile_staging.py`
2. `python3 scripts/build_blm_public_roads.py`
3. both Tippecanoe build scripts
4. both PMTiles conversion scripts

After step 4, browser-facing files are synced into `vector_tiles/`. The Pages
workflow publishes the Forest Service and official BLM sources and manifests.

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
- Replace the disabled supplemental-road experiment only if an authoritative,
  legally meaningful access dataset becomes available
- Tune low-zoom styling/generalization now that PMTiles loading is working locally
