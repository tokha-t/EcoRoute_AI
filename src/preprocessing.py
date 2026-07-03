from __future__ import annotations

import pandas as pd

CATEGORICAL_COLUMNS = ["district", "waste_type"]
NUMERIC_COLUMNS = [
    "capacity_liters",
    "previous_fill_pct",
    "hours_since_collection",
    "day_of_week",
    "is_weekend",
    "temperature",
    "rain_flag",
    "nearby_activity_score",
    "historical_daily_fill_rate",
]
FEATURE_COLUMNS = CATEGORICAL_COLUMNS + NUMERIC_COLUMNS
TARGET_COLUMN = "target_fill_pct"


def prepare_features(df: pd.DataFrame, include_target: bool = True):
    """Return model features and, when requested, the target vector."""
    X = df[FEATURE_COLUMNS].copy()
    if include_target:
        y = df[TARGET_COLUMN].copy()
        return X, y
    return X

