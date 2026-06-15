# CA Forest Service Roads

Interactive California forest-road map with:

- Forest Service MVUM/NFSR road overlays
- linked PDF MVUM sheets
- place search
- supplemental public-road overlay from OpenStreetMap when zoomed in
- static road tiles for faster pan/zoom on GitHub Pages
- static public-road tiles where cached, with live fallback elsewhere

## Local Development

Run:

```bash
./serve.sh
```

Then open:

```text
http://127.0.0.1:8080
```

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

The brown supplemental-road overlay now prefers pre-generated tiles from `_public_roads_tiles/` plus `public_roads_tiles_manifest.json`.
If the local public-road cache does not cover the current view yet, the app falls back to a live Overpass fetch for that area.
The checked-in cache is an initial seed and can be expanded over time by rerunning the fetch/build scripts.

To refresh the local OSM public-road cache:

```bash
bash scripts/fetch_public_roads_raw.sh
python3 scripts/build_public_roads_tiles.py
```
