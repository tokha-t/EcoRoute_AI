from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from src.optimize.distances import (
    DETOUR_FACTOR,
    FALLBACK_SPEED_KMH,
    apply_road_distances,
    get_matrix,
    haversine_meters,
)

DEPOT = (51.1694, 71.4491)
POINT_A = (51.1605, 71.4704)
POINT_B = (51.1801, 71.4102)

OSRM_2X2_PAYLOAD = {
    "code": "Ok",
    "durations": [[0.0, 120.5], [130.2, 0.0]],
    "distances": [[0.0, 1520.3], [1610.7, 0.0]],
}


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _refuse_network(*args, **kwargs):
    raise AssertionError("network must not be hit on this path")


class HaversineTest(unittest.TestCase):
    def test_one_degree_along_equator(self) -> None:
        # 1 degree of longitude at the equator is ~111.195 km
        self.assertAlmostEqual(haversine_meters((0.0, 0.0), (0.0, 1.0)), 111_195, delta=25)

    def test_london_to_new_york(self) -> None:
        london = (51.5074, -0.1278)
        new_york = (40.7128, -74.0060)
        self.assertAlmostEqual(haversine_meters(london, new_york), 5_570_000, delta=15_000)

    def test_symmetric_and_zero_on_identical_points(self) -> None:
        self.assertEqual(haversine_meters(DEPOT, DEPOT), 0.0)
        self.assertAlmostEqual(
            haversine_meters(DEPOT, POINT_A), haversine_meters(POINT_A, DEPOT), places=6
        )


class FallbackMatrixTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_dir = Path(self._tmp.name)

    def test_unreachable_osrm_falls_back_to_haversine_with_detour(self) -> None:
        with patch("src.optimize.distances.requests.get", side_effect=requests.ConnectionError):
            matrix = get_matrix([DEPOT, POINT_A], cache_dir=self.cache_dir)

        self.assertTrue(matrix.fallback_used)
        self.assertEqual(matrix.source, "haversine")
        expected_m = haversine_meters(DEPOT, POINT_A) * DETOUR_FACTOR
        self.assertAlmostEqual(matrix.meters[0][1], expected_m, places=6)
        self.assertAlmostEqual(matrix.meters[1][0], expected_m, places=6)
        self.assertEqual(matrix.meters[0][0], 0.0)
        self.assertAlmostEqual(
            matrix.seconds[0][1], expected_m / (FALLBACK_SPEED_KMH / 3.6), places=6
        )

    def test_osrm_error_code_falls_back(self) -> None:
        payload = {"code": "TooBig", "message": "Too many table coordinates"}
        with patch("src.optimize.distances.requests.get", return_value=_FakeResponse(payload)):
            matrix = get_matrix([DEPOT, POINT_A], cache_dir=self.cache_dir)

        self.assertTrue(matrix.fallback_used)
        self.assertEqual(matrix.source, "haversine")

    def test_fallback_result_is_not_cached(self) -> None:
        with patch("src.optimize.distances.requests.get", side_effect=requests.ConnectionError):
            get_matrix([DEPOT, POINT_A], cache_dir=self.cache_dir)

        self.assertEqual(list(self.cache_dir.glob("*.json")), [])


class OSRMTableTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_dir = Path(self._tmp.name)

    def test_parses_canned_table_response(self) -> None:
        with patch(
            "src.optimize.distances.requests.get", return_value=_FakeResponse(OSRM_2X2_PAYLOAD)
        ) as mock_get:
            matrix = get_matrix([DEPOT, POINT_A], cache_dir=self.cache_dir)

        self.assertFalse(matrix.fallback_used)
        self.assertEqual(matrix.source, "osrm")
        self.assertEqual(matrix.seconds, OSRM_2X2_PAYLOAD["durations"])
        self.assertEqual(matrix.meters, OSRM_2X2_PAYLOAD["distances"])

        url = mock_get.call_args.args[0]
        self.assertIn("/table/v1/driving/", url)
        # OSRM wants longitude,latitude order
        self.assertIn("71.449100,51.169400;71.470400,51.160500", url)
        self.assertEqual(mock_get.call_args.kwargs["params"], {"annotations": "duration,distance"})

    def test_null_entries_are_replaced_with_haversine_estimate(self) -> None:
        payload = {
            "code": "Ok",
            "durations": [[0.0, None], [130.2, 0.0]],
            "distances": [[0.0, None], [1610.7, 0.0]],
        }
        with patch("src.optimize.distances.requests.get", return_value=_FakeResponse(payload)):
            matrix = get_matrix([DEPOT, POINT_A], cache_dir=self.cache_dir)

        self.assertFalse(matrix.fallback_used)
        expected_m = haversine_meters(DEPOT, POINT_A) * DETOUR_FACTOR
        self.assertAlmostEqual(matrix.meters[0][1], expected_m, places=6)
        self.assertAlmostEqual(
            matrix.seconds[0][1], expected_m / (FALLBACK_SPEED_KMH / 3.6), places=6
        )
        self.assertEqual(matrix.meters[1][0], 1610.7)

    def test_trivial_point_sets_skip_network(self) -> None:
        with patch("src.optimize.distances.requests.get", side_effect=_refuse_network):
            empty = get_matrix([], cache_dir=self.cache_dir)
            single = get_matrix([DEPOT], cache_dir=self.cache_dir)

        self.assertEqual(empty.meters, [])
        self.assertEqual(single.meters, [[0.0]])
        # <2 points is labeled "trivial", never "osrm": the UI badge treats only
        # source=="osrm" as real road distances, so trivial must not masquerade.
        self.assertEqual(empty.source, "trivial")
        self.assertEqual(single.source, "trivial")
        self.assertFalse(empty.fallback_used)
        self.assertFalse(single.fallback_used)


class CacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_dir = Path(self._tmp.name)

    def test_second_call_hits_cache_without_network(self) -> None:
        with patch(
            "src.optimize.distances.requests.get", return_value=_FakeResponse(OSRM_2X2_PAYLOAD)
        ):
            first = get_matrix([DEPOT, POINT_A], cache_dir=self.cache_dir)

        self.assertEqual(len(list(self.cache_dir.glob("osrm_driving_*.json"))), 1)

        with patch("src.optimize.distances.requests.get", side_effect=_refuse_network):
            second = get_matrix([DEPOT, POINT_A], cache_dir=self.cache_dir)

        self.assertEqual(second.seconds, first.seconds)
        self.assertEqual(second.meters, first.meters)
        self.assertFalse(second.fallback_used)
        self.assertEqual(second.source, "osrm")

    def test_different_coordinate_sets_use_different_cache_entries(self) -> None:
        with patch(
            "src.optimize.distances.requests.get", return_value=_FakeResponse(OSRM_2X2_PAYLOAD)
        ):
            get_matrix([DEPOT, POINT_A], cache_dir=self.cache_dir)
            get_matrix([DEPOT, POINT_B], cache_dir=self.cache_dir)

        self.assertEqual(len(list(self.cache_dir.glob("osrm_driving_*.json"))), 2)


class ApplyRoadDistancesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_dir = Path(self._tmp.name)

        depot_point = {"bin_id": "Depot", "latitude": DEPOT[0], "longitude": DEPOT[1]}
        point_a = {"bin_id": "BIN-1", "latitude": POINT_A[0], "longitude": POINT_A[1]}
        point_b = {"bin_id": "BIN-2", "latitude": POINT_B[0], "longitude": POINT_B[1]}
        self.route_comparison = {
            "route_points_fixed": [depot_point, point_a, point_b, depot_point],
            "route_points_greedy": [depot_point, point_b, depot_point],
            "route_points_optimized": [depot_point, point_b, depot_point],
        }
        # Unique points in first-seen order: depot(0), A(1), B(2)
        self.meters_3x3 = [
            [0.0, 2000.0, 3000.0],
            [2100.0, 0.0, 5000.0],
            [3100.0, 5100.0, 0.0],
        ]
        self.payload = {
            "code": "Ok",
            "durations": [[0.0, 200.0, 300.0], [210.0, 0.0, 500.0], [310.0, 510.0, 0.0]],
            "distances": self.meters_3x3,
        }

    def test_distances_recomputed_from_road_meters(self) -> None:
        with patch("src.optimize.distances.requests.get", return_value=_FakeResponse(self.payload)):
            updated = apply_road_distances(self.route_comparison, cache_dir=self.cache_dir)

        expected_fixed_km = (2000.0 + 5000.0 + 3100.0) / 1000
        expected_selected_km = (3000.0 + 3100.0) / 1000
        self.assertAlmostEqual(updated["fixed_route_distance_km"], expected_fixed_km, places=3)
        self.assertAlmostEqual(
            updated["selected_optimized_distance_km"], expected_selected_km, places=3
        )
        self.assertAlmostEqual(
            updated["distance_saved_km"], expected_fixed_km - expected_selected_km, places=3
        )
        self.assertEqual(updated["distance_source"], "osrm")
        self.assertFalse(updated["fallback_used"])
        # Route point sequences themselves are untouched
        self.assertEqual(
            updated["route_points_optimized"], self.route_comparison["route_points_optimized"]
        )

    def test_fallback_flags_surface_for_ui_badge(self) -> None:
        with patch("src.optimize.distances.requests.get", side_effect=requests.ConnectionError):
            updated = apply_road_distances(self.route_comparison, cache_dir=self.cache_dir)

        self.assertEqual(updated["distance_source"], "haversine")
        self.assertTrue(updated["fallback_used"])
        expected_selected_km = (
            (haversine_meters(DEPOT, POINT_B) + haversine_meters(POINT_B, DEPOT))
            * DETOUR_FACTOR
            / 1000
        )
        self.assertAlmostEqual(
            updated["selected_optimized_distance_km"], expected_selected_km, places=3
        )


if __name__ == "__main__":
    unittest.main()
