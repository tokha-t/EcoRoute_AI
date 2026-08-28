from __future__ import annotations

import pandas as pd

from src.map_utils import create_simulation_map
from src.optimize.solver import Plan


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

    figure, source = create_simulation_map(
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
    assert all(trace.type == "scattermap" for trace in figure.data)
    assert figure.layout.map.style == "carto-positron"
