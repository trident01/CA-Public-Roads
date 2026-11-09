"""Classifier regression checks; these do not fetch data or rebuild tiles."""

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import build_verified_public_unpaved_roads as roads


class CandidateClassifierTests(unittest.TestCase):
    def feature(self, **properties):
        return {
            "type": "Feature",
            "properties": {"surface": "gravel", "source_detail": "osm way 123", **properties},
            "geometry": {"type": "LineString", "coordinates": [[-120, 38], [-119.99, 38]]},
        }

    def matched(self, feature):
        candidate = {"feature": feature, "coords": feature["geometry"]["coordinates"], "length_m": 1_000}
        reference = self.feature()
        return roads.verify_tile([candidate], [reference])

    def test_explicit_gravel_is_accepted_with_limited_evidence(self):
        accepted = self.matched(self.feature())
        self.assertEqual(len(accepted), 1)
        props = accepted[0]["properties"]
        self.assertEqual(props["caltrans_match_coverage"], 1.0)
        self.assertEqual(props["access_status"], "not_verified")
        self.assertEqual(props["match_evidence"], "geometry_proximity_only")

    def test_no_inferred_or_explicitly_paved_surfaces_reach_verifier(self):
        for surface in (None, "", "asphalt", "paved", "paving_stones", "gravel;asphalt"):
            with self.subTest(surface=surface):
                self.assertEqual(self.matched(self.feature(surface=surface)), [])
        self.assertEqual(self.matched(self.feature(surface_inferred=True)), [])

    def test_explicit_access_denials_are_rechecked_at_verifier(self):
        for access in (
            "no", "private", "destination", " PRIVATE ", "yes;no", "permit", "permit_required",
            "agricultural", "forestry", "customers", "delivery", "military",
            "employees", "restricted", "official", "emergency",
        ):
            with self.subTest(access=access):
                self.assertEqual(self.matched(self.feature(access=access)), [])

    def test_bicycle_access_can_coexist_with_motor_vehicle_prohibition(self):
        accepted = self.matched(self.feature(motor_vehicle="no", bicycle="yes"))
        self.assertEqual(len(accepted), 1)

    def test_vehicle_restriction_requires_explicit_recreation_allowance(self):
        for properties in (
            {"motor_vehicle": "permit"}, {"vehicle": "no"},
            {"motor_vehicle": "forestry", "bicycle": "no"},
            {"foot": "no", "bicycle": "private"},
        ):
            with self.subTest(properties=properties):
                self.assertEqual(self.matched(self.feature(**properties)), [])
        self.assertEqual(len(self.matched(self.feature(vehicle="no", foot="designated"))), 1)

    def test_osm_identity_is_stable_after_provenance_is_appended(self):
        for detail in ("osm way 123", "OpenStreetMap way 123; Caltrans All Roads geometry proximity match"):
            self.assertEqual(roads.source_id(self.feature(source_detail=detail)), "123")
        self.assertEqual(roads.source_id(self.feature(source_id="123", source_detail="OSM evidence")), "123")
        self.assertEqual(self.matched(self.feature())[0]["properties"]["osm_way_id"], "123")

    def test_explicit_pavement_contradictions_are_rejected(self):
        for properties in (
            {"paved": "yes"},
            {"surface:forward": "asphalt"},
            {"surface:lanes": "gravel|concrete"},
            {"surface:conditional": "asphalt @ (Nov-Mar)"},
        ):
            with self.subTest(properties=properties):
                self.assertEqual(self.matched(self.feature(**properties)), [])

    def test_track_grade_is_not_treated_as_surface_evidence(self):
        self.assertEqual(len(self.matched(self.feature(tracktype="grade1"))), 1)
        self.assertEqual(self.matched(self.feature(surface=None, tracktype="grade2")), [])

    def test_short_roads_cannot_bypass_length_threshold(self):
        feature = self.feature()
        feature["geometry"]["coordinates"] = [[-120, 38], [-119.999, 38]]
        self.assertEqual(self.matched(feature), [])

    def test_malformed_coordinates_fail_closed_without_geometry_errors(self):
        for coords in (
            [], [[-120, 38]], [[-120, 38], [-120, 38]],
            [[-120, 38], [float("nan"), 38]],
            [[-120, 38], [-119.99, float("inf")]],
            [[-120, 38], ["-119.99", 38]],
            [[-120, 38], [-119.99]],
            [[-120, 38], [-119.99, 100]],
            [[-120, 38], [True, 38]],
            [[[-120, 38], [-119.99, 38]]],
        ):
            with self.subTest(coords=coords):
                feature = self.feature()
                feature["geometry"]["coordinates"] = coords
                self.assertEqual(self.matched(feature), [])

    def test_unsupported_geometry_types_are_ignored(self):
        feature = self.feature()
        feature["geometry"]["type"] = "Polygon"
        self.assertEqual(self.matched(feature), [])
        self.assertEqual(roads.caltrans_lines([feature], 38), [])

    def test_caltrans_multiline_ignores_bad_parts(self):
        feature = self.feature()
        feature["geometry"] = {
            "type": "MultiLineString",
            "coordinates": [[[-120, 38], [-119.99, 38]], [[-120, 38]]],
        }
        self.assertEqual(len(roads.caltrans_lines([feature], 38)), 1)

    def test_current_distance_and_coverage_thresholds_are_preserved(self):
        candidate = roads.prepare_candidate(self.feature())
        far = self.feature()
        far["geometry"]["coordinates"] = [[lon, lat + 40 / 110_540] for lon, lat in candidate["coords"]]
        self.assertEqual(roads.verify_tile([candidate], [far]), [])
        partial = self.feature()
        partial["geometry"]["coordinates"][1][0] = -119.994
        self.assertEqual(roads.verify_tile([candidate], [partial]), [])
        self.assertEqual((roads.MIN_LENGTH_M, roads.MATCH_DISTANCE_M, roads.MIN_MATCH_COVERAGE), (500.0, 30.0, 0.85))

    def test_parallel_road_can_only_establish_proximity(self):
        # This known ambiguity is why accepted roads must not claim legal access.
        candidate = roads.prepare_candidate(self.feature())
        parallel = self.feature()
        parallel["geometry"]["coordinates"] = [[lon, lat + 20 / 110_540] for lon, lat in candidate["coords"]]
        result = roads.verify_tile([candidate], [parallel])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["properties"]["match_evidence"], "geometry_proximity_only")

    def test_long_way_matches_across_all_touched_tiles(self):
        feature = self.feature()
        feature["geometry"]["coordinates"] = [[-120.6, 38], [-119.4, 38]]
        candidate = roads.prepare_candidate(feature)
        refs = roads.matching_tile_refs(candidate["coords"])
        self.assertEqual(len(refs), 5)

        def reference_tile(x, y, refresh):
            west, _, east, _ = roads.tile_bounds(x, y)
            segment = self.feature()
            segment["properties"] = {"OBJECTID": x, "RouteId": "same-road"}
            segment["geometry"]["coordinates"] = [[max(west, -120.6), 38], [min(east, -119.4), 38]]
            # The same record may be returned by neighboring query envelopes.
            return [segment, segment]

        centroid_ref = roads.lonlat_to_tile(-120, 38)
        self.assertEqual(roads.verify_tile([candidate], reference_tile(*centroid_ref, False)), [])
        with patch.object(roads, "fetch_caltrans_tile", side_effect=reference_tile) as fetch:
            references = roads.fetch_candidate_caltrans_features([candidate], False)
        self.assertEqual(fetch.call_count, 5)
        self.assertEqual(len(references), 5)
        self.assertEqual(len(roads.verify_tile([candidate], references)), 1)

    def test_concurrent_cache_reads_fetch_and_write_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            tile = (7, 9)
            payloads = [{"count": 1}, {"features": [self.feature()]}]
            with patch.object(roads, "CACHE_DIR", Path(directory)), \
                    patch.object(roads, "request_json", side_effect=payloads) as request, \
                    patch.object(roads.time, "sleep"):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(lambda _: roads.fetch_caltrans_tile(*tile, False), range(2)))
            self.assertEqual(request.call_count, 2)
            self.assertEqual(results[0], results[1])
            self.assertEqual(json.loads((Path(directory) / "7/9.geojson").read_text())["features"], results[0])
            self.assertEqual(list(Path(directory).rglob("*.tmp")), [])

    def test_short_nonempty_caltrans_page_is_not_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(roads, "CACHE_DIR", Path(directory)), \
                    patch.object(roads, "request_json", side_effect=[{"count": 2}, {"features": [self.feature()]}]):
                with self.assertRaisesRegex(RuntimeError, "Incomplete"):
                    roads.fetch_caltrans_tile(8, 10, False)
            self.assertEqual(list(Path(directory).rglob("*.geojson")), [])

    def test_duplicate_caltrans_records_are_not_cached(self):
        reference = self.feature(OBJECTID=1)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(roads, "CACHE_DIR", Path(directory)), \
                    patch.object(roads, "request_json", side_effect=[{"count": 2}, {"features": [reference, reference]}]):
                with self.assertRaisesRegex(RuntimeError, "duplicate"):
                    roads.fetch_caltrans_tile(8, 11, False)
            self.assertEqual(list(Path(directory).rglob("*.geojson")), [])

    def test_incomplete_caltrans_cache_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "8"
            destination.mkdir()
            (destination / "12.geojson").write_text(json.dumps({"features": [self.feature()], "expected_feature_count": 2}))
            with patch.object(roads, "CACHE_DIR", Path(directory)):
                with self.assertRaisesRegex(RuntimeError, "Incomplete"):
                    roads.fetch_caltrans_tile(8, 12, False)

    def test_custom_file_loader_and_scope_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "roads.geojson"
            manifest_path = Path(directory) / "manifest.json"
            input_path.write_text(json.dumps({"type": "FeatureCollection", "features": [self.feature(), self.feature(access="private")]}))
            scope = {"kind": "california_osm_area", "description": "Statewide query, incomplete where unmapped"}
            manifest_path.write_text(json.dumps({"scope": scope, "fetched_utc": "2026-11-09T00:00:00Z"}))
            self.assertEqual(len(roads.load_candidates(None, input_file=input_path)), 1)
            provenance = roads.input_provenance(input_path, roads.INPUT_DIR, manifest_path)
            self.assertEqual(provenance["input_scope"], scope)
            self.assertIn("fetched_utc", provenance["input_source_metadata"])
            unknown = roads.input_provenance(input_path, roads.INPUT_DIR, None)
            self.assertEqual(unknown["input_scope"]["kind"], "custom_input")

    def test_legacy_input_scope_is_explicitly_not_statewide(self):
        provenance = roads.input_provenance(None, roads.INPUT_DIR, None)
        self.assertEqual(provenance["input_scope"]["kind"], "padded_forest_bounding_boxes")
        self.assertIn("not statewide", provenance["input_scope"]["description"])

    def test_reclassification_removes_restrictions_and_keeps_evidence_date(self):
        with tempfile.TemporaryDirectory() as directory:
            tiles = Path(directory) / "tiles"
            tiles.mkdir()
            manifest = Path(directory) / "manifest.json"
            metadata = {
                "built_utc": "2026-08-22T22:05:24Z",
                "criteria": {"minimum_length_m": 500.0, "caltrans_match_distance_m": 30.0,
                             "minimum_caltrans_match_coverage": 0.85},
            }
            manifest.write_text(json.dumps(metadata))
            features = [
                self.feature(caltrans_match_coverage=1.0),
                self.feature(source_detail="osm way 124", access="permit", caltrans_match_coverage=1.0),
            ]
            (tiles / "roads.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))
            with patch.object(roads, "MANIFEST_PATH", manifest), patch.object(roads, "OUTPUT_DIR", tiles):
                accepted, prior = roads.reclassify_existing_matches(None)
                self.assertEqual(len(accepted), 1)
                self.assertEqual(prior["built_utc"], "2026-08-22T22:05:24Z")
                metadata["criteria"]["caltrans_match_distance_m"] = 50
                manifest.write_text(json.dumps(metadata))
                with self.assertRaises(ValueError):
                    roads.reclassify_existing_matches(None)

    def test_invalid_optional_bboxes_are_rejected(self):
        for bbox in ("nan,32,-114,42", "-124,32,inf,42", "-190,32,-114,42", "-114,32,-124,42"):
            with self.subTest(bbox=bbox), self.assertRaises(ValueError):
                roads.parse_bbox(bbox)


if __name__ == "__main__":
    unittest.main()
