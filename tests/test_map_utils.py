from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from src.map_utils import create_simulation_map
from src.optimize.distances import RouteGeometry
from src.optimize.solver import Plan, Route


def test_simulation_map_uses_current_token_free_plotly_api() -> None:
    world = pd.DataFrame(
        [
            {
                "site_id": "S1",
                "lat": 51.17,
                "lon": 71.40,
                "address": "Test address",
                "capacity_liters": 1100,
                "fill_pct": 50.0,
                "daily_fill_rate_pct": 10.0,
                "days_since_service": 1,
                "klass": "YELLOW",
                "reason": "none",
            }
        ]
    )

    figure, source, straight_segments = create_simulation_map(
        world,
        Plan(
            routes=[],
            violations=[],
            dropped_site_ids=[],
            total_distance_m=0.0,
            total_duration_s=0.0,
            distance_source="none",
            fallback_used=False,
        ),
        depot=(51.1735, 71.4010),
        landfill=(51.1160, 71.3570),
    )

    assert source == "straight"
    assert straight_segments == 0
    assert all(trace.type == "scattermap" for trace in figure.data)
    assert figure.layout.map.style == "carto-positron"


def test_simulation_map_draws_final_landfill_before_depot() -> None:
    world = pd.DataFrame(
        [
            {
                "site_id": "S1",
                "lat": 51.2,
                "lon": 71.4,
                "address": "Test",
                "capacity_liters": 1100,
                "fill_pct": 80.0,
                "daily_fill_rate_pct": 10.0,
                "days_since_service": 2,
                "klass": "RED",
                "reason": "high_fill",
            }
        ]
    )
    depot = (51.1, 71.3)
    landfill = (51.0, 71.2)
    route = Route("T1", ["S1"], 100, 100, 100, ordered_stops=["S1", "LANDFILL"])
    plan = Plan([route], [], [], 100, 100, "test", False)
    captured: list[list[tuple[float, float]]] = []

    def straight(points):
        captured.append(list(points))
        return RouteGeometry(list(points), "straight")

    with patch("src.map_utils.get_route_geometry", side_effect=straight):
        create_simulation_map(world, plan, depot, landfill)
    assert captured == [[depot, (51.2, 71.4), landfill, depot]]
