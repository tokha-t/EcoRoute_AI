from __future__ import annotations

import pandas as pd

from src.optimize.distances import DistanceMatrix
from src.optimize.solver import SolverParams, Truck, plan_routes


def line_matrix(points: list[tuple[float, float]]) -> DistanceMatrix:
    meters = [[0.0] * len(points) for _ in points]
    seconds = [[0.0] * len(points) for _ in points]
    for i, a in enumerate(points):
        for j, b in enumerate(points):
            distance = ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
            meters[i][j] = distance
            seconds[i][j] = distance / 10
    return DistanceMatrix(seconds, meters, False, "test")


def sites(rows: list[tuple[str, float, float, float, float, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["site_id", "lat", "lon", "capacity_liters", "fill_pct", "klass"],
    ).assign(
        containers=1,
        container_liters=lambda df: df["capacity_liters"],
        address="test",
        district="test",
        area_type="mixed",
        daily_fill_rate_pct=10,
        last_service_day=0,
        source_real=False,
        must_serve=lambda df: df["klass"].eq("RED"),
    )


def solve(
    frame: pd.DataFrame,
    capacity: float = 5_000,
    max_dumps: int = 4,
    shift_duration_s: float = 100_000,
    detour_budget_m_per_m3: float = 1_200.0,
):
    depot = (0.0, 0.0)
    landfill = (0.0, 500.0)
    points = [depot] + list(zip(frame["lat"], frame["lon"])) + [landfill]
    params = SolverParams(
        depot=depot,
        landfill=landfill,
        matrix=line_matrix(points),
        max_dump_trips=max_dumps,
        shift_duration_s=shift_duration_s,
        detour_budget_m_per_m3=detour_budget_m_per_m3,
        time_limit_s=0.25,
    )
    return plan_routes(frame, [Truck("T1", capacity_kg=capacity)], params)


def test_dump_trips_reset_capacity_and_all_red_are_served() -> None:
    frame = sites(
        [
            ("R1", 0, 1000, 1000, 100, "RED"),
            ("R2", 0, 2000, 1000, 100, "RED"),
            ("R3", 0, 3000, 1000, 100, "RED"),
        ]
    )
    plan = solve(frame, capacity=150)
    assert plan.violations == []
    assert plan.routes[0].dump_stops == 3
    assert plan.routes[0].max_segment_load_kg <= 150
    assert plan.routes[0].end_load_kg == 0
    assert plan.routes[0].ordered_stops[-1] == "LANDFILL"
    assert sorted(plan.routes[0].site_ids) == ["R1", "R2", "R3"]
    assert plan.unserved_red == []


def test_yellow_two_pass_on_path_far_small_and_far_large() -> None:
    small_frame = sites(
        [
            ("R", 0, 2000, 1000, 70, "RED"),
            ("ON_PATH", 0, 1000, 1000, 30, "YELLOW"),
            ("FAR_SMALL", 3000, 1000, 100, 21, "YELLOW"),
        ]
    )
    plan = solve(small_frame)
    assert plan.violations == []
    assert "ON_PATH" in plan.served_yellow
    assert "FAR_SMALL" not in plan.served_yellow
    skipped = {decision.site_id: decision for decision in plan.skipped_yellow}
    assert skipped["FAR_SMALL"].insertion_cost_m > skipped["FAR_SMALL"].penalty_m
    assert skipped["FAR_SMALL"].explanation_ru
    assert plan.routes[0].site_ids.count("R") == 1

    large_frame = sites(
        [
            ("R", 0, 2000, 1000, 70, "RED"),
            ("FAR_LARGE", 3000, 1000, 40000, 21, "YELLOW"),
        ]
    )
    assert "FAR_LARGE" in solve(large_frame).served_yellow


def test_yellow_budget_is_a_hard_insertion_cost_gate() -> None:
    frame = sites(
        [
            ("R", 0, 2000, 1000, 70, "RED"),
            ("NEAR", 100, 1000, 1000, 100, "YELLOW"),
        ]
    )
    low_budget = solve(frame, detour_budget_m_per_m3=5.0)
    high_budget = solve(frame, detour_budget_m_per_m3=20.0)
    assert "NEAR" not in low_budget.served_yellow
    assert "NEAR" in high_budget.served_yellow
    skipped = {decision.site_id: decision for decision in low_budget.skipped_yellow}
    assert skipped["NEAR"].insertion_cost_m > skipped["NEAR"].penalty_m


def test_named_infeasibility_for_single_red() -> None:
    frame = sites([("TOO_HEAVY", 0, 1000, 10000, 100, "RED")])
    plan = solve(frame, capacity=500)
    assert plan.routes == []
    assert plan.unserved_red == ["TOO_HEAVY"]
    assert any("TOO_HEAVY" in violation for violation in plan.violations)


def test_single_site_returns_empty_via_landfill() -> None:
    plan = solve(sites([("ONLY", 0, 1000, 1000, 100, "RED")]), max_dumps=1)
    assert plan.violations == []
    assert plan.routes[0].ordered_stops == ["ONLY", "LANDFILL"]
    assert plan.routes[0].end_load_kg == 0


def test_named_infeasibility_when_landfill_return_exceeds_shift() -> None:
    plan = solve(
        sites([("REMOTE", 0, 1000, 1000, 100, "RED")]),
        max_dumps=1,
        shift_duration_s=100,
    )
    assert plan.routes == []
    assert plan.unserved_red == ["REMOTE"]
    assert any("REMOTE" in violation and "полигон" in violation for violation in plan.violations)
