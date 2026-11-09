# California Dirt Roads

A map for exploring California dirt and gravel roads, with surface and access
evidence available on click. It is a road-discovery tool, not a routing engine or
a live closure feed.

The interface keeps search and a small road key visible. **Layers** contains
road toggles, a known-unpaved forest-road filter, forest PDFs, data limitations,
and renderer settings. The URL retains the map view and selected layers so a
shared link opens the same view. Location is requested only when its button is
pressed.

## Run locally

```bash
./serve.sh
```

Open <http://127.0.0.1:8080>. The server supports HTTP Range requests for PMTiles
and concurrent tile requests. It uses `.venv-pmtiles/bin/python` when present,
otherwise `python3`; `PYTHON_BIN` and `PORT` can override those defaults.

Vector rendering is the default. `?mode=classic` uses the Leaflet fallback.
Both renderers support the same layer controls, road evidence, forest PDFs, and
shared views. Classic loads forest and candidate GeoJSON only from zoom 9 to
avoid downloading the whole state at startup. Road details are available from
zoom 11. Forest boundaries are context, not parcel ownership or access maps.

## What the roads mean

| Appearance | Source and meaning |
|---|---|
| Solid brown | Riverside County roads explicitly inventoried as maintained dirt or gravel |
| Dashed green | Forest Service motor-vehicle road inventory with an explicit unpaved surface |
| Dashed brown | OpenStreetMap unpaved road candidates near Caltrans road geometry |
| Muted gray | Other Forest Service roads: paved or surface not recorded |

Neither a motor-vehicle designation nor road maintenance establishes bike or
foot permission. Access fields are shown when recorded; missing fields stay
**Not recorded**. Local signs, closures, seasonal restrictions, and private
inholdings still matter. An empty part of the map does not establish that no
public roads exist there.

### Forest Service roads

Existing MVUM/NFSR geometry is in `_roads_geojson/`, classic tiles in
`_roads_tiles/`, and vector tiles in `vector_tiles/forest_roads.pmtiles`.
All forests are enabled initially in both renderers. Angeles has no interactive
road geometry; Modoc and Six Rivers have very sparse coverage. The forest
panel provides the 119 included MVUM PDFs, including these gaps.

```bash
python3 scripts/generate_road_tiles.py
python3 scripts/build_vector_tile_staging.py
bash scripts/tippecanoe_build.sh
bash scripts/pmtiles_convert.sh
```

### County-maintained unpaved roads

The first county source is Riverside County's official
[Unpaved Roads (County Maintained)](https://gis.countyofriverside.us/arcgis_mapping/rest/services/Transportation/04_Online_Road_Book_Mapping_v2/MapServer/1)
inventory. The initial addition contains 425 segments: 335 dirt and 90 gravel.

The builder accepts only `C07` (graveled surface maintained) and `C09` (dirt
surface maintained). Mixed status, abandoned, merely traveled, non-county, and
`TRAVEL_DIRECTIONS=N` records are excluded. This is county-specific coverage,
not a statewide county-road inventory, and supplies no bike/foot permissions.

```bash
python3 scripts/build_county_roads.py
```

Output is `_county_roads/riverside.geojson` plus a manifest recording the source,
fetch date, field definitions, selection rule, counts, bounds, and SHA256.

### Unpaved road candidates: conservative, not verified access

A candidate must have:

- An explicit supported OSM unpaved surface, never a surface inferred from road
  class or track grade.
- At least 500 metres of mapped geometry.
- At least 85% of that geometry within 30 metres of Caltrans All Roads geometry.
- No restricted general-access value, inferred-surface flag, or explicit paved
  contradiction in retained surface fields.
- No prohibition on both biking and walking. Motor/vehicle restrictions require
  an explicit positive bicycle or foot tag to retain the candidate.

**The geometry match does not establish road identity or legal access.** A
separate road 20 metres away can match at 100%; a regression test records this
limitation. OSM surface observations can also be stale. The UI therefore calls
these **Unpaved road candidates**, never verified public roads. The historical
`verified_public_roads` filenames remain for compatibility.

The older input `_public_roads_tiles/10` was cropped to padded forest bounding
boxes. It was not statewide, even with `scope_bbox: null`. Manifests now record
the actual input scope, OSM snapshot metadata, and the Caltrans match date.
Reclassifying stored matches preserves the original match date rather than
pretending to refresh the source. The audit removed six existing candidates
with permit/agricultural/forestry general-access tags.

A new resumable statewide source fetch avoids the forest crop and retains
bicycle, foot, general, motor-vehicle, and conditional access fields. It queries
explicitly unpaved minor road classes in the California OSM administrative area;
it does not add untagged tracks. Complete source extraction is still distinct
from complete road coverage. See the published manifests for the scope of the
currently displayed build.

The November 9, 2026 statewide build contains **16,754 candidates**, compared
with 7,394 after the initial legacy-data cleanup. Of those, 11,223 lie wholly
outside the supplied national-forest boundaries, including 8,556 newly added
roads. Boundary exclusion describes geography, not land ownership or access.
The build retains 7,376 previous candidates, adds 9,378, and drops 18 after
refreshing source tags and applying the current geometry checks.

```bash
python3 scripts/fetch_statewide_unpaved_roads.py
python3 scripts/build_verified_public_unpaved_roads.py \
  --input-file build/statewide_unpaved/candidates.geojson \
  --input-manifest build/statewide_unpaved/candidates_manifest.json \
  --workers 4
bash scripts/tippecanoe_build_verified_public_roads.sh
bash scripts/pmtiles_convert_verified_public_roads.sh
```

The fetcher caches completed latitude bands under `build/statewide_unpaved/`,
rejects incomplete Overpass responses, and records per-response timestamps.
`--endpoint` selects another Overpass service; `--band` creates an explicitly
partial extraction for investigation. Partial extraction is not a statewide
release. The matcher supports `--output-root` for isolated staging and uses a
resumable cache under `build/caltrans_all_roads_cache/`.
Reference geometry is gathered from every tile crossed by a road, fixing the
former centroid-only lookup that missed matches on long or curved roads.

To reapply current eligibility checks to the existing matched dataset without
refetching Caltrans, use `--reclassify-existing`, then rebuild its vector tiles.
This preserves the old Caltrans evidence date. It cannot discover new roads.

Source: [Caltrans Roadway Data](https://dot.ca.gov/programs/traffic-operations/office-of-traffic-safety-and-data/roadway-data).

## Validation and publishing

```bash
python3 -m unittest discover -s scripts -p 'test_*.py'
node scripts/test_map_ui.mjs
node scripts/test_cameras.mjs
python3 scripts/check_site_assets.py
python3 scripts/check_vector_preview_outputs.py
```

Python geometry builders/tests require Shapely. Vector generation requires
Tippecanoe and the PMTiles CLI. The existing `.venv-pmtiles` environment includes
the Python tooling used for local data work.

The GitHub Pages workflow validates the exact runtime asset list before
packaging, then validates the staged artifact again. It includes the HTML,
UI assets, vendor assets, enabled road layers and manifests, forest boundaries,
PDF catalog, and all linked PDFs. Missing files, corrupt tile headers,
inconsistent catalogs, and county checksum mismatches fail the build.
Raw downloads, source staging, and disabled experiments are not published.
Push to `main` or run **Deploy GitHub Pages** to deploy.

## Retained experiments

`vector_preview.html` is a development preview of the original vector pipeline.
The former broad OSM road overlay and fragmented BLM GTLF overlay remain disabled
and are excluded from publication. Their scripts and local data are retained
for research; they should not be enabled as an undifferentiated public-road
network. In particular, missing access restrictions are not positive access
evidence, and forest bounding boxes are not land ownership polygons.

## ALERTCalifornia cameras

The camera layer uses the official
[ALERTCalifornia public map feed](https://services8.arcgis.com/X84q166Srnyl4JMV/ArcGIS/rest/services/ALERTCalifornia_Camera_Feed/FeatureServer/0).
It is enabled initially but makes no queries below zoom 6. At zooms 6–10 it
shows compact camera markers without requesting images, including in the
default statewide view. At zoom 11 and above,
it shows at most eight full-image thumbnails, with collision avoidance and
colocated camera grouping. Clicking a marker or thumbnail opens that camera's
ALERTCalifornia page, including its history tools.

Queries cover the visible area, respect the feed's 50-record page limit, cache
recent results, and abort when the view changes. There is no background image
polling. Missing/offline images retain a usable camera link. The displayed view
time is reported by the source; imagery is not a live closure or road-condition
feed. Disabling cameras is saved in shared map URLs.

Images remain uncropped, including the source information bar and partner
marks. Credit is **ALERTCalifornia | UC San Diego**. The provider's
[imagery/data terms](https://alertcalifornia.org/terms/) use CC BY-NC-ND 4.0;
[crediting requirements](https://alertcalifornia.org/crediting-and-branding/)
apply. Source images are displayed directly, not copied into this repository.

```bash
node scripts/test_cameras.mjs
```
