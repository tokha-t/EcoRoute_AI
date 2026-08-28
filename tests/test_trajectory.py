from __future__ import annotations

import pickle
import time
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from src.optimize.distances import DistanceMatrix
from src.optimize.solver import Plan, Truck
from src.sim.fill import ClassificationParams
from src.sim.trajectory import (
    TrajectoryCacheMismatch,
    TrajectoryParams,
    build_trajectory,
    get_snapshot,
    solve_day,
    trajectory_cache_path,
)


def world() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "site_id": "S1",
                "lat": 51.2,
                "lon": 71.2,
                "address": "Test",
                "containers": 1,
                "container_liters": 1100,
                "capacity_liters": 1100,
                "area_type": "mixed",
                "daily_fill_rate_pct": 10.0,
                "fill_pct": 20.0,
                "last_service_day": 0,
                "source_real": True,
            }
        ]
    )


def params() -> TrajectoryParams:
    return TrajectoryParams(
        trucks=(Truck("T1", 5000),),
        classification=ClassificationParams(max_interval_days=99),
        depot=(51.1, 71.1),
        landfill=(51.3, 71.3),
    )


def matrix() -> DistanceMatrix:
    return DistanceMatrix(
        seconds=[[0, 10, 20], [10, 0, 10], [20, 10, 0]],
        meters=[[0, 100, 200], [100, 0, 100], [200, 100, 0]],
        fallback_used=False,
        source="test",
    )


def empty_solver(*args, **kwargs) -> Plan:
    return Plan([], [], [], 0, 0, "test", False)


def test_trajectory_is_deterministic_and_lookup_never_calls_solver(tmp_path: Path) -> None:
    first_solver = Mock(side_effect=empty_solver)
    progress_events: list[tuple[int, int]] = []
    first = build_trajectory(
        world(),
        params(),
        days=3,
        seed=7,
        cache_dir=tmp_path,
        matrix=matrix(),
        solver=first_solver,
        progress=lambda day, days: progress_events.append((day, days)),
    )
    calls_after_build = first_solver.call_count
    assert get_snapshot(first, 3).day == 3
    assert get_snapshot(first, 0).day == 0
    assert first_solver.call_count == calls_after_build
    assert progress_events == [(0, 3), (1, 3), (2, 3), (3, 3)]

    started = time.perf_counter()
    for _ in range(1_000):
        assert get_snapshot(first, 0).day == 0
        assert get_snapshot(first, 3).day == 3
        assert get_snapshot(first, 0).day == 0
    assert time.perf_counter() - started < 0.2

    second = build_trajectory(
        world(),
        params(),
        days=3,
        seed=7,
        cache_dir=tmp_path,
        solver=Mock(side_effect=AssertionError("disk cache must skip solver")),
    )
    for left, right in zip(first, second):
        pd.testing.assert_frame_equal(left.state_df, right.state_df)
        pd.testing.assert_frame_equal(left.classified_df, right.classified_df)


def test_disk_cache_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    data = world()
    settings = params()
    build_trajectory(data, settings, days=1, cache_dir=tmp_path, matrix=matrix(), solver=empty_solver)
    path = trajectory_cache_path(data, settings, 1, 42, tmp_path)
    with path.open("rb") as handle:
        envelope = pickle.load(handle)
    envelope["trajectory_key"] = "wrong"
    with path.open("wb") as handle:
        pickle.dump(envelope, handle)
    with pytest.raises(TrajectoryCacheMismatch, match="hash mismatch"):
        build_trajectory(data, settings, days=1, cache_dir=tmp_path, matrix=matrix())


def test_manual_override_changes_only_the_requested_day_solve() -> None:
    captured: list[list[str]] = []

    def capture_solver(sites, trucks, solver_params):
        captured.append(sites["site_id"].astype(str).tolist())
        return empty_solver()

    classified, plan = solve_day(
        world(),
        0,
        params(),
        matrix=matrix(),
        overrides={"S1": "include"},
        solver=capture_solver,
    )
    assert captured == [["S1"]]
    assert classified.loc[0, "manual_override"] == "include"
    assert plan.manual_overrides == {"S1": "include"}

    captured.clear()
    classified, plan = solve_day(
        world(),
        0,
        params(),
        matrix=matrix(),
        overrides={"S1": "exclude"},
        solver=capture_solver,
    )
    assert captured == []
    assert classified.loc[0, "manual_override"] == "exclude"
    assert plan.manual_overrides == {"S1": "exclude"}
