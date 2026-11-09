"""Guard the official maintenance/access exclusions without a network request."""

import unittest

from build_county_roads import normalize_feature


def road(code="C07", direction="B", geometry=None):
    return {
        "type": "Feature",
        "properties": {"OBJECTID": 7, "STNAME": "  Test Road  ", "TYPE": code,
                       "RDNUMBER": "123", "TRAVEL_DIRECTIONS": direction},
        "geometry": geometry or {"type": "LineString", "coordinates": [[-116.5, 33.5], [-116.4, 33.6]]},
    }


class CountyRoadFilterTests(unittest.TestCase):
    def test_explicit_county_maintained_gravel_and_dirt_are_kept(self):
        for code, surface in (("C07", "gravel"), ("C09", "dirt")):
            with self.subTest(code=code):
                result = normalize_feature(road(code))
                self.assertEqual(result["properties"]["surface"], surface)
                self.assertEqual(result["properties"]["name"], "Test Road")
                self.assertEqual(result["properties"]["road_id"], "riverside-7")
                self.assertTrue(result["properties"]["url"].startswith("https://gis.countyofriverside.us/"))
                self.assertNotIn("bicycle", result["properties"])
                self.assertNotIn("foot", result["properties"])

    def test_ambiguous_noncounty_vacated_and_traveled_codes_are_excluded(self):
        for code in (None, "", "C05", "C08", "C10", "C11", "C13", "C14", "C15",
                     "C21", "C22", "C23", "C24", "C25", "C26", "W06"):
            with self.subTest(code=code):
                self.assertIsNone(normalize_feature(road(code)))

    def test_no_ways_is_excluded(self):
        self.assertIsNone(normalize_feature(road(direction="N")))

    def test_one_way_direction_is_preserved(self):
        self.assertEqual(normalize_feature(road(direction="FT"))["properties"]["travel_direction"], "FT")

    def test_projected_or_invalid_geometry_cannot_escape_as_geojson(self):
        for geometry in (
            {"type": "Point", "coordinates": [-116.5, 33.5]},
            {"type": "LineString", "coordinates": [[6129957, 2095313], [6129958, 2095314]]},
            {"type": "LineString", "coordinates": [[-116.5, 33.5]]},
            {"type": "LineString", "coordinates": [[-116.5, 33.5], [-116.5, 33.5]]},
        ):
            with self.subTest(geometry=geometry), self.assertRaises(ValueError):
                normalize_feature(road(geometry=geometry))

    def test_multiline_geometry_is_supported(self):
        geometry = {"type": "MultiLineString", "coordinates": [
            [[-116.5, 33.5], [-116.4, 33.6]], [[-116.4, 33.6], [-116.3, 33.7]]
        ]}
        self.assertEqual(normalize_feature(road(geometry=geometry))["geometry"], geometry)


if __name__ == "__main__":
    unittest.main()
