from __future__ import annotations

import pandas as pd

from src.optimize.distances import DistanceMatrix
from src.optimize.solver import SolverParams, Truck, plan_routes
from src.reports.route_sheet import build_route_sheet


def test_route_sheet_preserves_order_and_final_landfill() -> None:
    sites = pd.DataFrame(
        [
            {
                "site_id": "RED-1",
                "lat": 0.0,
                "lon": 100.0,
                "capacity_liters": 1000,
                "fill_pct": 100.0,
                "klass": "RED",
                "must_serve": True,
                "address": "ул. Тестовая, 1",
                "containers": 1,
                "reason": "high_fill",
            }
        ]
    )
    matrix = DistanceMatrix(
        seconds=[[0, 10, 20], [10, 0, 10], [20, 10, 0]],
        meters=[[0, 100, 200], [100, 0, 100], [200, 100, 0]],
        fallback_used=False,
        source="test",
    )
    plan = plan_routes(
        sites,
        [Truck("T1", capacity_kg=500)],
        SolverParams(
            depot=(0.0, 0.0),
            landfill=(0.0, 200.0),
            matrix=matrix,
            shift_duration_s=10_000,
            max_dump_trips=1,
            time_limit_s=0.2,
        ),
    )
    sheet = build_route_sheet(plan, sites)
    planned = sheet.rows[sheet.rows["status"] == "в маршруте"]
    assert list(planned["stop_type"]) == ["DEPOT", "SITE", "LANDFILL", "DEPOT"]
    assert list(planned["site_id"])[1] == "RED-1"
    assert plan.routes[0].ordered_stops[-1] == "LANDFILL"
    assert "RED-1" in sheet.html
    assert "Водитель" in sheet.html
