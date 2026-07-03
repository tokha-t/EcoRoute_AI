from __future__ import annotations

import time
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
import requests

from src.optimize.distances import DistanceMatrix, haversine_meters
from src.optimize.solver import (
    DENSITY_KG_PER_L,
    Plan,
    SolverParams,
    Truck,
    estimate_load_kg,
    plan_routes,
)

DEPOT = (51.1694, 71.4491)
DETOUR = 1.4
SPEED_MPS = 25.0 / 3.6

# One full 1100 L container: 1 * 1100 * 1.0 * 0.12 = 132 kg
FULL_1100L_KG = 1100 * DENSITY_KG_PER_L


def build_test_matrix(points: list[tuple[float, float]]) -> DistanceMatrix:
    """Deterministic haversine matrix ordered [depot] + sites (no network)."""
    n = len(points)
    meters = [[0.0] * n for _ in range(n)]
    seconds = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                meters[i][j] = haversine_meters(points[i], points[j]) * DETOUR
                seconds[i][j] = meters[i][j] / SPEED_MPS
    return DistanceMatrix(seconds=seconds, meters=meters, fallback_used=False, source="test")


def make_sites(count: int, must_serve: bool | list[bool] = False, spread: float = 0.01) -> pd.DataFrame:
    """Full 1100 L sites (132 kg each) spaced around the depot."""
    if isinstance(must_serve, bool):
        must_serve = [must_serve] * count
    return pd.DataFrame(
        {
            "bin_id": [f"BIN-{i:04d}" for i in range(1, count + 1)],
            "latitude": [DEPOT[0] + spread * (i % 5 - 2) for i in range(count)],
            "longitude": [DEPOT[1] + spread * (i // 5 - 2) for i in range(count)],
            "capacity_liters": [1100] * count,
            "predicted_fill_pct": [100.0] * count,
            "must_serve": must_serve,
        }
    )


def make_params(sites_df: pd.DataFrame, **overrides) -> SolverParams:
    points = [DEPOT] + [(float(r.latitude), float(r.longitude)) for r in sites_df.itertuples()]
    defaults = {"depot": DEPOT, "time_limit_s": 0.5, "matrix": build_test_matrix(points)}
    defaults.update(overrides)
    return SolverParams(**defaults)


def served_site_ids(plan: Plan) -> list[str]:
    return [site_id for route in plan.routes for site_id in route.site_ids]


class LoadEstimateTest(unittest.TestCase):
    def test_formula_with_containers_column(self) -> None:
        df = pd.DataFrame(
            {"containers": [2], "capacity_liters": [1100], "predicted_fill_pct": [50.0]}
        )
        # 2 * 1100 * 0.5 * 0.12 = 132
        self.assertAlmostEqual(estimate_load_kg(df).iloc[0], 132.0)

    def test_containers_default_to_one(self) -> None:
        df = pd.DataFrame({"capacity_liters": [1100], "predicted_fill_pct": [100.0]})
        self.assertAlmostEqual(estimate_load_kg(df).iloc[0], FULL_1100L_KG)

    def test_missing_fill_is_treated_as_full(self) -> None:
        df = pd.DataFrame({"capacity_liters": [240], "predicted_fill_pct": [None]})
        self.assertAlmostEqual(estimate_load_kg(df).iloc[0], 240 * DENSITY_KG_PER_L)


class CapacityTest(unittest.TestCase):
    def test_capacity_never_exceeded(self) -> None:
        # 6 optional sites x 132 kg, trucks of 300 kg -> at most 2 sites per truck.
        sites = make_sites(6)
        trucks = [Truck("T1", capacity_kg=300), Truck("T2", capacity_kg=300)]

        plan = plan_routes(sites, trucks, make_params(sites))

        self.assertEqual(plan.violations, [])
        capacity_by_truck = {truck.truck_id: truck.capacity_kg for truck in trucks}
        for route in plan.routes:
            self.assertLessEqual(route.load_kg, capacity_by_truck[route.truck_id])
            self.assertAlmostEqual(route.load_kg, FULL_1100L_KG * len(route.site_ids))
        self.assertEqual(len(served_site_ids(plan)) + len(plan.dropped_site_ids), 6)
        self.assertEqual(len(plan.dropped_site_ids), 2)

    def test_demand_over_one_truck_uses_both_trucks(self) -> None:
        # The milestone's verify scenario: 4 must-serve sites x 132 kg = 528 kg
        # total, one 300 kg truck cannot carry it -> both trucks route.
        sites = make_sites(4, must_serve=True)
        trucks = [Truck("T1", capacity_kg=300), Truck("T2", capacity_kg=300)]

        plan = plan_routes(sites, trucks, make_params(sites))

        self.assertEqual(plan.violations, [])
        self.assertEqual(len(plan.routes), 2)
        self.assertTrue(all(route.site_ids for route in plan.routes))
        self.assertEqual(sorted(served_site_ids(plan)), sorted(sites["bin_id"]))
        self.assertEqual(plan.dropped_site_ids, [])


class MustServeTest(unittest.TestCase):
    def test_must_serve_always_in_solution(self) -> None:
        # Capacity forces drops, but only optional sites may be dropped.
        sites = make_sites(8, must_serve=[True, False, True, False, True, False, False, False])
        trucks = [Truck("T1", capacity_kg=300), Truck("T2", capacity_kg=300)]

        plan = plan_routes(sites, trucks, make_params(sites))

        self.assertEqual(plan.violations, [])
        served = set(served_site_ids(plan))
        must_ids = set(sites[sites["must_serve"]]["bin_id"])
        self.assertTrue(must_ids.issubset(served))
        self.assertTrue(set(plan.dropped_site_ids).isdisjoint(must_ids))

    def test_must_serve_over_fleet_capacity_is_a_violation(self) -> None:
        sites = make_sites(3, must_serve=True)  # 396 kg
        trucks = [Truck("T1", capacity_kg=150), Truck("T2", capacity_kg=150)]

        plan = plan_routes(sites, trucks, make_params(sites))

        self.assertEqual(plan.routes, [])
        self.assertTrue(any("fleet capacity" in v for v in plan.violations))

    def test_single_must_serve_site_too_heavy_is_a_violation(self) -> None:
        sites = make_sites(1, must_serve=True)
        plan = plan_routes(sites, [Truck("T1", capacity_kg=100)], make_params(sites))

        self.assertEqual(plan.routes, [])
        self.assertTrue(any("BIN-0001" in v for v in plan.violations))

    def test_must_serve_beyond_shift_is_a_violation(self) -> None:
        sites = make_sites(1, must_serve=True)
        sites.loc[0, "latitude"] = DEPOT[0] + 5.0  # ~550 km away

        plan = plan_routes(sites, [Truck("T1")], make_params(sites, shift_duration_s=3600))

        self.assertEqual(plan.routes, [])
        self.assertTrue(any("shift" in v for v in plan.violations))


class ShiftDurationTest(unittest.TestCase):
    def test_routes_stay_within_shift(self) -> None:
        # Service time dominates: 600 s/stop, 1 h shift -> <=5 stops per truck.
        sites = make_sites(20)
        trucks = [Truck("T1"), Truck("T2")]
        params = make_params(sites, service_time_s=600.0, shift_duration_s=3600.0)

        plan = plan_routes(sites, trucks, params)

        self.assertEqual(plan.violations, [])
        self.assertTrue(plan.dropped_site_ids)
        for route in plan.routes:
            self.assertLessEqual(route.duration_s, 3600.0)


class EdgeCaseTest(unittest.TestCase):
    def test_empty_selection_returns_empty_plan(self) -> None:
        empty = pd.DataFrame()
        plan = plan_routes(empty, [Truck("T1")], SolverParams(depot=DEPOT))

        self.assertEqual(plan.routes, [])
        self.assertEqual(plan.violations, [])
        self.assertEqual(plan.dropped_site_ids, [])
        self.assertEqual(plan.total_distance_m, 0.0)

    def test_truck_count_is_validated(self) -> None:
        sites = make_sites(2)
        with self.assertRaises(ValueError):
            plan_routes(sites, [], make_params(sites))
        with self.assertRaises(ValueError):
            plan_routes(sites, [Truck(f"T{i}") for i in range(4)], make_params(sites))

    def test_missing_columns_are_reported(self) -> None:
        sites = make_sites(2).drop(columns=["bin_id"])
        with self.assertRaises(ValueError):
            plan_routes(sites, [Truck("T1")], make_params(sites))

    def test_route_metrics_match_matrix(self) -> None:
        # One truck, one site: distance and duration are the exact round trip.
        sites = make_sites(1)
        params = make_params(sites)
        plan = plan_routes(sites, [Truck("T1")], params)

        self.assertEqual(len(plan.routes), 1)
        route = plan.routes[0]
        expected_m = params.matrix.meters[0][1] + params.matrix.meters[1][0]
        expected_s = params.matrix.seconds[0][1] + params.matrix.seconds[1][0] + params.service_time_s
        self.assertAlmostEqual(route.distance_m, expected_m, places=6)
        self.assertAlmostEqual(route.duration_s, expected_s, places=6)
        self.assertEqual(plan.total_distance_m, route.distance_m)

    def test_osrm_outage_falls_back_and_is_flagged(self) -> None:
        sites = make_sites(2)
        params = SolverParams(depot=DEPOT, time_limit_s=0.5)  # no injected matrix
        with patch("src.optimize.distances.requests.get", side_effect=requests.ConnectionError):
            plan = plan_routes(sites, [Truck("T1")], params)

        self.assertEqual(plan.violations, [])
        self.assertTrue(plan.fallback_used)
        self.assertEqual(plan.distance_source, "haversine")


class ScaleTest(unittest.TestCase):
    def test_300_sites_two_trucks_solve_under_30_seconds(self) -> None:
        rng = np.random.default_rng(42)
        count = 300
        must_serve = np.zeros(count, dtype=bool)
        must_serve[rng.choice(count, size=10, replace=False)] = True
        sites = pd.DataFrame(
            {
                "bin_id": [f"BIN-{i:04d}" for i in range(1, count + 1)],
                "latitude": rng.uniform(51.05, 51.30, count),
                "longitude": rng.uniform(71.25, 71.65, count),
                "capacity_liters": rng.choice([120, 240, 660, 1100], count),
                "predicted_fill_pct": rng.uniform(30, 100, count),
                "containers": rng.integers(1, 5, count),
                "must_serve": must_serve,
            }
        )
        trucks = [Truck("T1"), Truck("T2")]
        params = make_params(sites, time_limit_s=5.0)

        started = time.monotonic()
        plan = plan_routes(sites, trucks, params)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 30.0)
        self.assertEqual(plan.violations, [])
        served = set(served_site_ids(plan))
        self.assertTrue(set(sites[sites["must_serve"]]["bin_id"]).issubset(served))
        capacity_by_truck = {truck.truck_id: truck.capacity_kg for truck in trucks}
        for route in plan.routes:
            self.assertLessEqual(route.load_kg, capacity_by_truck[route.truck_id])


if __name__ == "__main__":
    unittest.main()
