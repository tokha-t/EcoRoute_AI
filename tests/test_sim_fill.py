from __future__ import annotations

import numpy as np
import pandas as pd

from src.sim.fill import ClassificationParams, advance_day, classify


def frame(fill: list[float], rate: list[float], last: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "site_id": [f"S{index}" for index in range(len(fill))],
            "fill_pct": fill,
            "daily_fill_rate_pct": rate,
            "last_service_day": last,
            "area_type": ["multistorey"] * len(fill),
        }
    )


def test_exact_classification_rules_and_precedence() -> None:
    sites = frame([10, 51, 51, 70, 20], [3, 51, 10, 3, 3], [-5, 0, 0, 0, -3])
    result = classify(sites, day=0)

    assert list(result["klass"]) == ["RED", "RED", "YELLOW", "RED", "RED"]
    assert list(result["reason"]) == [
        "max_interval",
        "overflow_predicted",
        "none",
        "high_fill",
        "max_interval",
    ]


def test_horizon_three_projects_each_next_day() -> None:
    sites = frame([21], [25], [0])
    result = classify(sites, day=0, params=ClassificationParams(planning_horizon_days=3))
    assert result.iloc[0]["projected_fill_pct"] == 96
    assert result.iloc[0]["klass"] == "YELLOW"


def test_advance_day_is_deterministic_and_keeps_overflow() -> None:
    sites = frame([99], [10], [0])
    first = advance_day(sites, day=1, rng=np.random.default_rng(42))
    second = advance_day(sites, day=1, rng=np.random.default_rng(42))
    pd.testing.assert_frame_equal(first, second)
    assert first.iloc[0]["fill_pct"] > 100
