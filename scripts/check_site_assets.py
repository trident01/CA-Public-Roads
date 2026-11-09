#!/usr/bin/env python3
"""Validate the map's local runtime assets and list exactly what Pages needs.

Run from any directory. --root can also point at a prepared Pages artifact;
--write-file-list produces a sorted rsync --files-from list after validation.
Only enabled road layers are included. Pipeline inputs, staging GeoJSON,
MBTiles, and disabled experimental layers are deliberately not packaged.
"""

import argparse
import hashlib
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit


REPO_ROOT = Path(__file__).resolve().parent.parent
ASSET_LITERAL = re.compile(
    r'''(?P<quote>["'`])(?P<url>[A-Za-z0-9_./][^<>"'`\s]*\.(?:json|geojson|pmtiles|pdf|js|css|png|svg|webp)(?:[?#][^<>"'`\s]*)?)(?P=quote)'''
)
CSS_URL = re.compile(r'''url\(\s*["']?([^\s)"']+)["']?\s*\)''')
LAYERS = (
    (None, "roads_tiles_manifest.json", "_roads_tiles", "ROAD_TILE_ZOOM",
     "VECTOR_MANIFEST_URL", "VECTOR_PMTILES_URL"),
    ("ENABLE_VERIFIED_PUBLIC_ROADS", "verified_public_roads_tiles_manifest.json",
     "_verified_public_roads_tiles", "VERIFIED_PUBLIC_ROADS_TILE_ZOOM",
     "VERIFIED_PUBLIC_VECTOR_MANIFEST_URL", "VERIFIED_PUBLIC_VECTOR_PMTILES_URL"),
    ("ENABLE_EXPERIMENTAL_OSM_ROADS", "public_roads_tiles_manifest.json",
     "_public_roads_tiles", "PUBLIC_ROADS_TILE_ZOOM",
     "PUBLIC_VECTOR_MANIFEST_URL", "PUBLIC_VECTOR_PMTILES_URL"),
    ("ENABLE_BLM_ROADS", "blm_public_roads_tiles_manifest.json",
     "_blm_roads_tiles", "BLM_ROADS_TILE_ZOOM",
     "BLM_VECTOR_MANIFEST_URL", "BLM_VECTOR_PMTILES_URL"),
)


class AssetError(ValueError):
    pass


class HTMLAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if value and ((tag == "script" and key == "src")
                          or (tag == "link" and key == "href")
                          or (tag in {"img", "source"} and key == "src")):
                self.urls.append(value)


class SiteAssets:
    def __init__(self, root):
        self.root = root.resolve()
        self.files = set()
        self.pdfs = set()
        self.tiles = 0
        self.html = self.read("index.html")

    def add(self, name):
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise AssetError(f"unsafe local asset path: {name}")
        name = path.as_posix()
        local = self.root / name
        if not local.is_file() or local.resolve().is_relative_to(self.root) is False:
            raise AssetError(f"missing or unsafe asset: {name}")
        if any(parent.is_symlink() for parent in [local, *local.parents]
               if parent != self.root and parent.is_relative_to(self.root)):
            raise AssetError(f"symlink is not a portable Pages asset: {name}")
        if name != ".nojekyll" and local.stat().st_size == 0:
            raise AssetError(f"empty asset: {name}")
        self.files.add(name)
        return local

    def read(self, name):
        return self.add(name).read_text(encoding="utf-8")

    def json(self, name):
        try:
            return json.loads(self.read(name))
        except json.JSONDecodeError as exc:
            raise AssetError(f"invalid JSON in {name}: {exc}") from exc

    def constant(self, name):
        match = re.search(rf"\bconst\s+{re.escape(name)}\s*=\s*", self.html)
        if not match:
            raise AssetError(f"index.html is missing runtime constant {name}")
        try:
            return json.JSONDecoder().raw_decode(self.html[match.end():])[0]
        except json.JSONDecodeError as exc:
            raise AssetError(f"{name} must be a JSON literal for asset validation") from exc

    def local_url(self, url, base=""):
        parts = urlsplit(url)
        if parts.scheme or parts.netloc or not parts.path:
            return None
        if "${" in parts.path:
            # These URLs are expanded from the active tile manifests below.
            for _, _, tile_dir, zoom, _, _ in LAYERS:
                if parts.path == f"{tile_dir}/${{{zoom}}}/${{x}}/${{y}}.geojson":
                    return None
            raise AssetError(f"unvalidated dynamic local asset URL: {url}")
        return (PurePosixPath(base) / unquote(parts.path)).as_posix()

    def catalog(self):
        forests = self.constant("FORESTS")
        catalog = self.json("pdf_manifest.json")
        embedded = self.constant("EMBEDDED_CATALOG")
        if not isinstance(forests, list) or len(set(forests)) != len(forests):
            raise AssetError("FORESTS must contain unique forest IDs")
        for label, entries in (("pdf_manifest.json", catalog), ("EMBEDDED_CATALOG", embedded)):
            if not isinstance(entries, dict) or set(entries) != set(forests):
                raise AssetError(f"{label} must contain exactly the FORESTS catalog IDs")
            for forest, pdfs in entries.items():
                if not isinstance(pdfs, list) or not pdfs:
                    raise AssetError(f"{label}: {forest} has no PDF entries")
                seen = set()
                for pdf in pdfs:
                    if not isinstance(pdf, dict) or not str(pdf.get("label", "")).strip():
                        raise AssetError(f"{label}: invalid PDF label for {forest}")
                    href = pdf.get("href")
                    name = self.local_url(href) if isinstance(href, str) else None
                    if not name or not name.endswith(".pdf") or name in seen:
                        raise AssetError(f"{label}: invalid or duplicate PDF href for {forest}: {href}")
                    seen.add(name)
                    path = self.add(name)
                    with path.open("rb") as stream:
                        if stream.read(5) != b"%PDF-":
                            raise AssetError(f"not a PDF document: {name}")
                    self.pdfs.add(name)
        if catalog != embedded:
            raise AssetError("pdf_manifest.json and EMBEDDED_CATALOG are out of sync")

    def road_layers(self):
        disabled = set()
        for flag, manifest_name, tile_dir, zoom_name, vector_name, archive_name in LAYERS:
            enabled = True if flag is None else self.constant(flag)
            if not isinstance(enabled, bool):
                raise AssetError(f"{flag} must be true or false")
            vector_path = self.constant(vector_name)
            archive_path = self.constant(archive_name)
            if not enabled:
                disabled.update((manifest_name, vector_path, archive_path))
                continue
            manifest = self.json(manifest_name)
            tiles = manifest.get("tiles")
            zoom = self.constant(zoom_name)
            if (not isinstance(tiles, dict) or not tiles
                    or manifest.get("tile_count") != len(tiles)
                    or manifest.get("tile_zoom") != zoom):
                raise AssetError(f"invalid tile count or zoom in {manifest_name}")
            for key, metadata in tiles.items():
                if not re.fullmatch(rf"{zoom}/\d+/\d+", key):
                    raise AssetError(f"invalid tile key in {manifest_name}: {key}")
                data = self.json(f"{tile_dir}/{key}.geojson")
                features = data.get("features")
                if (data.get("type") != "FeatureCollection" or not isinstance(features, list)
                        or not features or len(features) != metadata.get("feature_count")):
                    raise AssetError(f"invalid feature collection/count: {tile_dir}/{key}.geojson")
                self.tiles += 1
            vector = self.json(vector_path)
            if (not vector.get("layer_name") or not vector.get("staged_feature_count")
                    or not isinstance(vector.get("min_zoom"), int)
                    or not isinstance(vector.get("max_zoom"), int)):
                raise AssetError(f"invalid vector manifest: {vector_path}")
            with self.add(archive_path).open("rb") as stream:
                if stream.read(8) != b"PMTiles\x03":
                    raise AssetError(f"invalid PMTiles v3 archive: {archive_path}")
        return disabled

    def validate(self):
        self.add(".nojekyll")
        self.catalog()
        disabled = self.road_layers()
        parser = HTMLAssets()
        parser.feed(self.html)
        pending = set(parser.urls)
        pending.update(match["url"] for match in ASSET_LITERAL.finditer(self.html))
        # Leaflet computes marker/retina image URLs at runtime, outside CSS.
        vendor = self.root / "vendor"
        if vendor.is_dir():
            pending.update(path.relative_to(self.root).as_posix()
                           for path in vendor.rglob("*") if path.is_file())
        scanned = set()
        while pending:
            url = min(pending)
            pending.remove(url)
            name = self.local_url(url)
            if name is None or name in disabled or name in scanned:
                continue
            scanned.add(name)
            path = self.add(name)
            if path.suffix in {".js", ".css"}:
                source = path.read_text(encoding="utf-8")
                if path.suffix == ".css":
                    for match in CSS_URL.finditer(source):
                        dependency = self.local_url(match[1], str(PurePosixPath(name).parent))
                        if dependency:
                            pending.add(dependency)
                elif not name.startswith("vendor/"):
                    pending.update(match["url"] for match in ASSET_LITERAL.finditer(source))
        # Validate the public-land overlay as data, rather than just its presence.
        for name in sorted(self.files):
            if name.startswith("_public_lands/") and name.endswith(".geojson"):
                data = self.json(name)
                if (data.get("type") != "FeatureCollection" or not data.get("features")
                        or any(feature.get("geometry", {}).get("type") not in {"Polygon", "MultiPolygon"}
                               for feature in data["features"])):
                    raise AssetError(f"invalid public-land boundary collection: {name}")
        if any(name.startswith("_county_roads/") for name in self.files):
            manifest = self.json("_county_roads/manifest.json")
            name = manifest.get("geojson")
            if not isinstance(name, str) or not name.startswith("_county_roads/"):
                raise AssetError("county-road manifest must identify its local GeoJSON")
            path = self.add(name)
            if (path.stat().st_size != manifest.get("geojson_bytes")
                    or hashlib.sha256(path.read_bytes()).hexdigest() != manifest.get("geojson_sha256")):
                raise AssetError("county-road data does not match its provenance manifest")
            data = self.json(name)
            features = data.get("features")
            if (data.get("type") != "FeatureCollection" or not isinstance(features, list)
                    or not features or len(features) != manifest.get("feature_count")):
                raise AssetError("county-road feature count does not match its provenance manifest")
            ids = set()
            for feature in features:
                props = feature.get("properties", {})
                road_id = props.get("road_id")
                if (not road_id or road_id in ids or not props.get("name")
                        or props.get("type_code") not in {"C07", "C09"}
                        or props.get("surface") != {"C07": "gravel", "C09": "dirt"}.get(props.get("type_code"))
                        or props.get("url") != manifest.get("source_url")
                        or props.get("agency") != manifest.get("agency")
                        or props.get("travel_direction") == "N"
                        or feature.get("geometry", {}).get("type") not in {"LineString", "MultiLineString"}):
                    raise AssetError(f"invalid or unsupported county-road provenance: {road_id}")
                ids.add(road_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT,
                        help="repository or prepared site directory (default: repository)")
    parser.add_argument("--write-file-list", type=Path,
                        help="write sorted rsync --files-from paths after validation")
    args = parser.parse_args()
    try:
        site = SiteAssets(args.root)
        site.validate()
        names = sorted(site.files)
        total = sum((site.root / name).stat().st_size for name in names)
        pdf_bytes = sum((site.root / name).stat().st_size for name in site.pdfs)
        if args.write_file_list:
            args.write_file_list.write_text("\n".join(names) + "\n", encoding="utf-8")
        print(f"Validated {len(names)} runtime files: {len(site.pdfs)} PDFs, {site.tiles} road tiles")
        print(f"Site: {total:,} bytes ({total / 1024**2:.1f} MiB); PDFs: {pdf_bytes / 1024**2:.1f} MiB")
        return 0
    except (AssetError, OSError, TypeError, AttributeError) as exc:
        print(f"Site asset validation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
