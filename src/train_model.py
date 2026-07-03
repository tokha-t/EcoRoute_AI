from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from src.data_generator import TRAINING_DATA_PATH, ensure_data_exists
from src.preprocessing import CATEGORICAL_COLUMNS, NUMERIC_COLUMNS, prepare_features

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = PROJECT_ROOT / "models"
MODEL_PATH = MODELS_DIR / "fill_level_model.pkl"
METRICS_PATH = MODELS_DIR / "metrics.json"


def _build_pipeline() -> Pipeline:
    preprocessor = ColumnTransformer(
        transformers=[
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                CATEGORICAL_COLUMNS,
            ),
            ("numeric", "passthrough", NUMERIC_COLUMNS),
        ]
    )

    model = RandomForestRegressor(
        n_estimators=200,
        max_depth=12,
        random_state=42,
        n_jobs=-1,
    )

    return Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            ("model", model),
        ]
    )


def train_and_save_model():
    """Train the fill-level model, save artifacts, and return model plus metrics."""
    ensure_data_exists()
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    training_df = pd.read_csv(TRAINING_DATA_PATH)
    X, y = prepare_features(training_df, include_target=True)
    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.2,
        random_state=42,
    )

    pipeline = _build_pipeline()
    pipeline.fit(X_train, y_train)

    predictions = np.clip(pipeline.predict(X_test), 0, 100)
    mse = mean_squared_error(y_test, predictions)
    metrics = {
        "mae": round(float(mean_absolute_error(y_test, predictions)), 3),
        "rmse": round(float(np.sqrt(mse)), 3),
        "r2": round(float(r2_score(y_test, predictions)), 3),
        "train_rows": int(len(X_train)),
        "test_rows": int(len(X_test)),
        "model_name": "RandomForestRegressor",
        "generated_at": datetime.now(UTC).isoformat(),
    }

    joblib.dump(pipeline, MODEL_PATH)
    METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    return pipeline, metrics


if __name__ == "__main__":
    trained_model, trained_metrics = train_and_save_model()
    print(json.dumps(trained_metrics, indent=2))

