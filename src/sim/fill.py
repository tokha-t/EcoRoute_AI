"""Daily accumulation and exact-priority classification for SPEC V2 §4."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.config import (
    MAX_INTERVAL_DAYS,
    OVERFLOW_LIMIT,
    PLANNING_HORIZON_DAYS,
    RED_THRESHOLD,
    YELLOW_THRESHOLD,
)
from src.sim.world import weekday_factor


@dataclass(frozen=True)
class ClassificationParams:
    red_threshold: float = RED_THRESHOLD
    yellow_threshold: float = YELLOW_THRESHOLD
    overflow_limit: float = OVERFLOW_LIMIT
    planning_horizon_days: int = PLANNING_HORIZON_DAYS
    max_interval_days: int = MAX_INTERVAL_DAYS


def advance_day(world_df: pd.DataFrame, day: int, rng: np.random.Generator) -> pd.DataFrame:
    """Accumulate one day of waste without clipping overflow above 100%."""
    result = world_df.copy()
    factors = np.array([weekday_factor(area, day) for area in result["area_type"]], dtype=float)
    jitter = np.clip(rng.normal(1.0, 0.08, size=len(result)), 0.75, 1.25)
    result["fill_pct"] = (
        result["fill_pct"].astype(float).to_numpy()
        + result["daily_fill_rate_pct"].astype(float).to_numpy() * factors * jitter
    )
    return result


def _projected_rates(world_df: pd.DataFrame, day: int, horizon: int) -> np.ndarray:
    sums = np.array(
        [
            sum(weekday_factor(area, day + offset) for offset in range(1, horizon + 1))
            for area in world_df["area_type"]
        ],
        dtype=float,
    )
    return world_df["daily_fill_rate_pct"].astype(float).to_numpy() * sums


def classify(
    world_df: pd.DataFrame,
    day: int,
    params: ClassificationParams | None = None,
) -> pd.DataFrame:
    """Apply §4.2 in priority order and attach reason/explainability fields."""
    params = params or ClassificationParams()
    if params.planning_horizon_days < 1:
        raise ValueError("planning_horizon_days must be at least 1")
    result = world_df.copy()
    fill = result["fill_pct"].astype(float).to_numpy()
    days_since = day - result["last_service_day"].astype(int).to_numpy()
    projected = fill + _projected_rates(result, day, params.planning_horizon_days)

    overdue = days_since >= params.max_interval_days
    high = fill >= params.red_threshold
    overflow = projected >= params.overflow_limit
    yellow = fill >= params.yellow_threshold
    klass = np.full(len(result), "GREEN", dtype=object)
    reason = np.full(len(result), "none", dtype=object)
    klass[yellow] = "YELLOW"
    klass[overflow] = "RED"
    reason[overflow] = "overflow_predicted"
    klass[high] = "RED"
    reason[high] = "high_fill"
    klass[overdue] = "RED"
    reason[overdue] = "max_interval"

    result["days_since_service"] = days_since
    result["projected_fill_pct"] = projected
    result["klass"] = klass
    result["reason"] = reason
    result["must_serve"] = klass == "RED"
    return result


def empty_sites(world_df: pd.DataFrame, site_ids: list[str], day: int) -> pd.DataFrame:
    result = world_df.copy()
    mask = result["site_id"].astype(str).isin({str(value) for value in site_ids})
    result.loc[mask, "fill_pct"] = 0.0
    result.loc[mask, "last_service_day"] = int(day)
    return result
