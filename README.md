# CA Forest Service Roads

Interactive California forest-road map with:

- Forest Service MVUM/NFSR road overlays
- linked PDF MVUM sheets
- place search
- supplemental public-road overlay from OpenStreetMap when zoomed in

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
- `_roads_geojson/`
- forest PDF folders
- `vendor/`
