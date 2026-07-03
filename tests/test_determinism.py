"""Determinism regression tests (final QA milestone).

Every random source in the simulation pipeline is seeded, so two consecutive
runs must produce identical numbers — the demo's honesty story depends on
every figure being re-derivable from the repo. The CVRP solver is the one
component without an explicit seed: it runs guided local search under a
wall-clock limit, so this suite pins down that consecutive solves still
return the same plan.
"""

from __future__ import annotations

import unittest

import pandas as pd

from src.data_generator import _generate_common_features, _target_fill_pct
from src.optimize.solver import Truck, plan_routes
from tests.test_solver import make_params, make_sites


class DataGeneratorDeterminismTest(unittest.TestCase):
    def test_same_seed_gives_identical_features_and_target(self) -> None:
        first = _generate_common_features(n_rows=250, seed=42, bin_prefix="TRN")
        second = _generate_common_features(n_rows=250, seed=42, bin_prefix="TRN")

        pd.testing.assert_frame_equal(first, second)
        self.assertListEqual(
            _target_fill_pct(first, seed=42).tolist(),
            _target_fill_pct(second, seed=42).tolist(),
        )

    def test_different_seed_gives_different_data(self) -> None:
        first = _generate_common_features(n_rows=250, seed=42, bin_prefix="TRN")
        other = _generate_common_features(n_rows=250, seed=43, bin_prefix="TRN")

        self.assertFalse(first.drop(columns=["bin_id"]).equals(other.drop(columns=["bin_id"])))


class SolverDeterminismTest(unittest.TestCase):
    def test_consecutive_solves_return_identical_plans(self) -> None:
        # 30 sites, a third must-serve, capacity tight enough to force drops:
        # enough structure for local search to actually explore alternatives.
        sites = make_sites(30, must_serve=[i % 3 == 0 for i in range(30)])
        trucks = [Truck("T1", capacity_kg=1_000), Truck("T2", capacity_kg=1_000)]

        first = plan_routes(sites, trucks, make_params(sites))
        second = plan_routes(sites, trucks, make_params(sites))

        self.assertEqual(first.violations, [])
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
