from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from src.data_generator import DATA_DIR, ensure_data_exists
from src.preprocessing import FEATURE_COLUMNS, prepare_features
from src.train_model import MODEL_PATH, train_and_save_model


SAMPLE_PREDICTIONS_PATH = DATA_DIR / "sample_predictions.csv"

# Max-interval rule (P0, see SPEC_V1 6.4): a site overdue this many days is always
# Critical + must-serve, regardless of predicted fill. Never remove or weaken.
MAX_INTERVAL_DAYS = 3


def assign_priority(df: pd.DataFrame, threshold: int | float) -> pd.DataFrame:
    """Assign operational priority labels based on predicted fill level.

    The max-interval rule overrides the model: rows overdue >= MAX_INTERVAL_DAYS
    are Critical and must_serve regardless of predicted fill.
    """
    result = df.copy()
    if "predicted_fill_pct" not in result.columns:
        result["predicted_fill_pct"] = pd.Series(dtype=float)

    predicted = result["predicted_fill_pct"].astype(float)
    if "hours_since_collection" in result.columns:
        overdue = result["hours_since_collection"].astype(float) >= MAX_INTERVAL_DAYS * 24
    else:
        overdue = pd.Series(False, index=result.index)

    selected = predicted >= threshold
    result["priority"] = np.select(
        [
            overdue,
            selected & (predicted >= 90),
            selected & (predicted >= 80) & (predicted < 90),
            selected & (predicted < 80),
        ],
        ["Critical", "Critical", "High", "Medium"],
        default="Skip",
    )
    result["must_serve"] = (overdue | selected).astype(bool)
    return result


def _load_or_train_model():
    if not Path(MODEL_PATH).exists():
        train_and_save_model()
    try:
        return joblib.load(MODEL_PATH)
    except Exception:
        train_and_save_model()
        return joblib.load(MODEL_PATH)


def predict_fill_levels(
    bins_df: pd.DataFrame,
    threshold: int | float = 75,
    save_snapshot: bool = True,
) -> pd.DataFrame:
    """Predict current fill levels and optionally save the latest prediction snapshot."""
    ensure_data_exists()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    result = bins_df.copy()
    if result.empty:
        result["predicted_fill_pct"] = pd.Series(dtype=float)
        result["priority"] = pd.Series(dtype=str)
        result["must_serve"] = pd.Series(dtype=bool)
        if save_snapshot:
            result.to_csv(SAMPLE_PREDICTIONS_PATH, index=False)
        return result

    model = _load_or_train_model()
    X = prepare_features(result[FEATURE_COLUMNS], include_target=False)
    predictions = np.clip(model.predict(X), 0, 100)
    result["predicted_fill_pct"] = np.round(predictions, 2)
    result = assign_priority(result, threshold=threshold)
    if save_snapshot:
        result.to_csv(SAMPLE_PREDICTIONS_PATH, index=False)

    return result
