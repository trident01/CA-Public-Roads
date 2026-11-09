"""Source integrity checks for the statewide unpaved-road extractor."""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import fetch_statewide_unpaved_roads as fetch


def way(**tags):
    return {"type": "way", "id": 123, "tags": {"highway": "track", "surface": "gravel", **tags},
            "geometry": [{"lat": 34.0, "lon": -116.0}, {"lat": 34.1, "lon": -116.0}]}


class SourceIntegrityTests(unittest.TestCase):
    def test_unknown_and_mixed_surfaces_are_never_inferred(self):
        for surface in ("", "asphalt", "gravel;asphalt", "paved"):
            with self.subTest(surface=surface):
                self.assertIsNone(fetch.normalize_feature(way(surface=surface))[0])

    def test_explicit_unpaved_minor_road_is_not_lost_to_class(self):
        feature, _ = fetch.normalize_feature(way(highway="tertiary", surface="dirt"))
        self.assertEqual(feature["properties"]["road_class"], "tertiary")
        self.assertNotIn("surface_inferred", feature["properties"])
        self.assertIsNone(fetch.normalize_feature(way(highway="secondary"))[0])

    def test_legacy_paved_tag_survives_for_downstream_contradiction_rejection(self):
        # Real current way215350119 is tagged surface=gravel AND paved=yes.
        # Source slimming must not hide the contradiction from the verifier.
        feature, reason = fetch.normalize_feature(way(paved="yes", foot="yes"))
        self.assertIsNone(reason)
        self.assertEqual(feature["properties"]["surface"], "gravel")
        self.assertEqual(feature["properties"]["paved"], "yes")

    def test_restricted_general_access_is_excluded(self):
        for access in fetch.BLOCKED_ACCESS:
            with self.subTest(access=access):
                self.assertIsNone(fetch.normalize_feature(way(access=access, foot="yes"))[0])

    def test_vehicle_closure_with_explicit_recreation_access_is_retained(self):
        feature, _ = fetch.normalize_feature(way(motor_vehicle="no", bicycle="yes",
                                                 **{"bicycle:conditional": "no @ (wet)", "surface:conditional": "mud @ (wet)"}))
        self.assertEqual(feature["properties"]["motor_vehicle"], "no")
        self.assertEqual(feature["properties"]["bicycle:conditional"], "no @ (wet)")
        self.assertEqual(feature["properties"]["surface:conditional"], "mud @ (wet)")
        self.assertIsNone(fetch.normalize_feature(way(vehicle="no"))[0])
        self.assertIsNone(fetch.normalize_feature(way(foot="no", bicycle="private"))[0])

    def test_driveways_and_invalid_geometry_are_excluded(self):
        self.assertIsNone(fetch.normalize_feature(way(highway="service", service="driveway"))[0])
        element = way()
        element["geometry"][1]["lat"] = float("nan")
        self.assertIsNone(fetch.normalize_feature(element)[0])

    def test_partial_overpass_response_fails_even_with_features(self):
        payload = {"elements": [way()], "osm3s": {"timestamp_osm_base": "2026-11-09T00:00:00Z"},
                   "remark": "runtime error: Query timed out"}
        with self.assertRaises(ValueError):
            fetch.validate_response(payload)

    def test_band_overlap_is_deduplicated_by_osm_way_id(self):
        payload = {"elements": [way()], "osm3s": {"timestamp_osm_base": "2026-11-09T00:00:00Z"}}
        features, audit = fetch.build_candidates([payload, payload])
        self.assertEqual(len(features), 1)
        self.assertEqual(audit["unique_source_way_count"], 1)

    def test_query_has_real_california_area_and_explicit_surface(self):
        query = fetch.overpass_query(32.4, 33.4, 180, "2026-11-09T00:00:00Z")
        self.assertIn('["ISO3166-2"="US-CA"]', query)
        self.assertIn('[date:"2026-11-09T00:00:00Z"]', query)
        self.assertIn('["surface"~"^(', query)
        self.assertEqual(fetch.latitude_bands()[-1][1], 42.1)

    def test_timeout_fallback_keeps_only_complete_subqueries_and_deduplicates(self):
        left = {"elements": [way()], "osm3s": {"timestamp_osm_base": "2026-11-09T00:00:00Z"}}
        right = {"elements": [way()], "osm3s": {"timestamp_osm_base": "2026-11-09T00:01:00Z"}}
        with patch.object(fetch, "request_overpass", side_effect=[RuntimeError("HTTP 504"), left, right]):
            payload, sources = fetch.fetch_region("https://example.invalid", 32.4, 33.4, 30, 1, "curl", None)
        self.assertEqual(len(payload["elements"]), 1)
        self.assertEqual(len(sources), 2)
        self.assertEqual(len(fetch.response_timestamps(payload)), 2)

    def test_permission_or_rate_failure_does_not_spawn_more_queries(self):
        with patch.object(fetch, "request_overpass", side_effect=RuntimeError("HTTP 429")) as request:
            with self.assertRaises(RuntimeError):
                fetch.fetch_region("https://example.invalid", 32.4, 33.4, 30, 1, "curl", None)
        self.assertEqual(request.call_count, 1)

    def test_successful_subquery_is_reused_after_interruption(self):
        payload = {"elements": [way()], "osm3s": {"timestamp_osm_base": "2026-11-09T00:00:00Z"}}
        with tempfile.TemporaryDirectory() as temporary:
            cache_dir = Path(temporary)
            # Full band times out; left half completes; right half fails.
            with patch.object(fetch, "request_overpass", side_effect=[RuntimeError("HTTP 504"), payload, RuntimeError("HTTP 403")]):
                with self.assertRaises(RuntimeError):
                    fetch.fetch_region("https://example.invalid", 32.4, 32.9, 30, 1, "curl", None, cache_dir)
            # On resume only the full request and missing right half hit network.
            with patch.object(fetch, "request_overpass", side_effect=[RuntimeError("HTTP 504"), payload]) as request:
                result, sources = fetch.fetch_region("https://example.invalid", 32.4, 32.9, 30, 1, "curl", None, cache_dir)
            self.assertEqual(request.call_count, 2)
            self.assertEqual(len(result["elements"]), 1)
            self.assertEqual(len(sources), 2)


if __name__ == "__main__":
    unittest.main()
