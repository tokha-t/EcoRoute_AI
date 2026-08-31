from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from src.map_utils import create_simulation_map, simulation_stop_details
from src.optimize.distances import RouteGeometry, RouteSegment
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


def test_selected_truck_has_numbered_stops_arrows_and_dimmed_context() -> None:
    world = pd.DataFrame(
        [
            {
                "site_id": site_id,
                "lat": latitude,
                "lon": longitude,
                "address": site_id,
                "capacity_liters": 1100,
                "fill_pct": 80.0,
                "daily_fill_rate_pct": 10.0,
                "days_since_service": 2,
                "klass": "RED",
                "reason": "high_fill",
            }
            for site_id, latitude, longitude in (
                ("S1", 51.2, 71.4),
                ("S2", 51.21, 71.41),
            )
        ]
    )
    depot = (51.1, 71.3)
    landfill = (51.0, 71.2)
    routes = [
        Route("T1", ["S1"], 100, 100, 100, ordered_stops=["S1", "LANDFILL"]),
        Route("T2", ["S2"], 100, 100, 100, ordered_stops=["S2", "LANDFILL"]),
    ]
    plan = Plan(routes, [], [], 200, 200, "road_cache", False)

    def road_geometry(points):
        segments = [
            RouteSegment(a, b, [a, b], "road_cache", 100.0)
            for a, b in zip(points[:-1], points[1:])
        ]
        return RouteGeometry(list(points), "road_cache", segments)

    with patch("src.map_utils.get_route_geometry", side_effect=road_geometry):
        figure, source, straight_segments = create_simulation_map(
            world,
            plan,
            depot,
            landfill,
            selected_truck_id="T1",
            route_sheet_rows=pd.DataFrame(
                [
                    {
                        "site_id": "S1",
                        "truck_id": "T1",
                        "sequence": 1,
                        "eta": "08:05",
                        "cumulative_load_kg": 100.0,
                    }
                ]
            ),
        )

    assert source == "road_cache"
    assert straight_segments == 0
    order = next(trace for trace in figure.data if trace.name == "T1 stop order")
    assert list(order.customdata) == ["S1"]
    assert figure.layout.map.layers[0].symbol.text == "1"
    t2_lines = [
        trace
        for trace in figure.data
        if trace.mode == "lines" and "T2" in trace.name and "direction" not in trace.name
    ]
    assert t2_lines and all(trace.opacity == 0.18 for trace in t2_lines)
    assert any(trace.mode == "lines" and trace.name == "T1 direction" for trace in figure.data)
    assert not any(
        getattr(trace.marker, "symbol", None) in {"triangle", "square", "diamond"}
        for trace in figure.data
    )
    assert any(
        trace.mode == "lines" and trace.line.color == "#2563eb" for trace in figure.data
    )


def test_simulation_stop_details_include_eta_load_and_skip_reason() -> None:
    world = pd.DataFrame(
        [
            {
                "site_id": "S1",
                "address": "Test address",
                "klass": "YELLOW",
                "fill_pct": 55.0,
                "reason": "none",
            }
        ]
    )
    route_sheet = pd.DataFrame(
        [
            {
                "site_id": "S1",
                "truck_id": "T1",
                "sequence": 3,
                "eta": "09:15",
                "cumulative_load_kg": 850.0,
                "status": "в маршруте",
                "reason": "detour accepted",
            }
        ]
    )
    details = simulation_stop_details(world, route_sheet, "S1")
    assert details is not None
    assert details["eta"] == "09:15"
    assert details["load_kg"] == 850.0
    assert details["reason"] == "detour accepted"
