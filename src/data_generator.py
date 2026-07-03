from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
TRAINING_DATA_PATH = DATA_DIR / "training_data.csv"
BINS_PATH = DATA_DIR / "bins.csv"

ASTANA_LATITUDE = 51.1694
ASTANA_LONGITUDE = 71.4491

DISTRICTS = [
    "Downtown",
    "Residential",
    "Market",
    "Park",
    "School Zone",
    "Industrial",
    "Business Center",
]

WASTE_TYPES = ["General", "Plastic", "Paper", "Organic", "Glass"]
CAPACITIES = [120, 240, 660, 1100]

DISTRICT_ACTIVITY_FACTOR = {
    "Downtown": 1.28,
    "Residential": 1.00,
    "Market": 1.45,
    "Park": 0.90,
    "School Zone": 1.24,
    "Industrial": 0.78,
    "Business Center": 1.32,
}

WASTE_TYPE_FACTOR = {
    "General": 1.16,
    "Plastic": 1.02,
    "Paper": 0.95,
    "Organic": 1.20,
    "Glass": 0.72,
}

DISTRICT_COORD_OFFSETS = {
    "Downtown": (0.000, 0.000),
    "Residential": (0.030, -0.030),
    "Market": (-0.018, 0.025),
    "Park": (0.023, 0.035),
    "School Zone": (-0.030, -0.020),
    "Industrial": (0.045, 0.055),
    "Business Center": (-0.012, -0.040),
}

DISTRICT_ACTIVITY_MEAN = {
    "Downtown": 7.4,
    "Residential": 5.4,
    "Market": 8.8,
    "Park": 4.8,
    "School Zone": 7.1,
    "Industrial": 3.9,
    "Business Center": 7.8,
}


def _district_probabilities() -> list[float]:
    return [0.18, 0.24, 0.14, 0.11, 0.12, 0.09, 0.12]


def _generate_coordinates(rng: np.random.Generator, districts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    latitudes = []
    longitudes = []

    for district in districts:
        lat_offset, lon_offset = DISTRICT_COORD_OFFSETS[district]
        latitudes.append(ASTANA_LATITUDE + lat_offset + rng.normal(0, 0.010))
        longitudes.append(ASTANA_LONGITUDE + lon_offset + rng.normal(0, 0.015))

    return np.array(latitudes), np.array(longitudes)


def _generate_common_features(n_rows: int, seed: int, bin_prefix: str) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    districts = rng.choice(DISTRICTS, size=n_rows, p=_district_probabilities())
    waste_types = rng.choice(WASTE_TYPES, size=n_rows, p=[0.38, 0.20, 0.16, 0.18, 0.08])
    latitudes, longitudes = _generate_coordinates(rng, districts)
    day_of_week = rng.integers(0, 7, size=n_rows)
    is_weekend = np.isin(day_of_week, [5, 6]).astype(int)

    activity_base = np.array([DISTRICT_ACTIVITY_MEAN[d] for d in districts])
    nearby_activity_score = np.clip(rng.normal(activity_base, 1.1), 0, 10)

    capacity_liters = rng.choice(CAPACITIES, size=n_rows, p=[0.20, 0.34, 0.28, 0.18])
    previous_fill_pct = np.clip(rng.beta(2.0, 2.8, size=n_rows) * 100, 0, 100)
    hours_since_collection = rng.integers(1, 97, size=n_rows)
    temperature = np.round(rng.normal(14, 13, size=n_rows), 1)
    temperature = np.clip(temperature, -10, 35)
    rain_probability = np.where(np.isin(districts, ["Park", "Market"]), 0.24, 0.18)
    rain_flag = (rng.random(n_rows) < rain_probability).astype(int)

    district_factor = np.array([DISTRICT_ACTIVITY_FACTOR[d] for d in districts])
    waste_factor = np.array([WASTE_TYPE_FACTOR[w] for w in waste_types])
    capacity_factor = 1.05 - (capacity_liters / 1800)
    historical_daily_fill_rate = (
        5.5
        * district_factor
        * waste_factor
        * capacity_factor
        + nearby_activity_score * 0.55
        + rng.normal(0, 1.2, size=n_rows)
    )
    historical_daily_fill_rate = np.clip(historical_daily_fill_rate, 2.0, 24.0)

    return pd.DataFrame(
        {
            "bin_id": [f"{bin_prefix}-{i:04d}" for i in range(1, n_rows + 1)],
            "latitude": np.round(latitudes, 6),
            "longitude": np.round(longitudes, 6),
            "district": districts,
            "waste_type": waste_types,
            "capacity_liters": capacity_liters,
            "previous_fill_pct": np.round(previous_fill_pct, 2),
            "hours_since_collection": hours_since_collection,
            "day_of_week": day_of_week,
            "is_weekend": is_weekend,
            "temperature": temperature,
            "rain_flag": rain_flag,
            "nearby_activity_score": np.round(nearby_activity_score, 2),
            "historical_daily_fill_rate": np.round(historical_daily_fill_rate, 2),
        }
    )


def _target_fill_pct(df: pd.DataFrame, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed + 10_000)

    district_factor = df["district"].map(DISTRICT_ACTIVITY_FACTOR).to_numpy()
    waste_factor = df["waste_type"].map(WASTE_TYPE_FACTOR).to_numpy()
    hours_factor = df["hours_since_collection"].to_numpy() / 24
    activity_score = df["nearby_activity_score"].to_numpy()
    daily_rate = df["historical_daily_fill_rate"].to_numpy()

    weekend_effect = np.where(df["is_weekend"].to_numpy() == 1, 2.3, 0.0)
    park_weekend_boost = np.where(
        (df["district"].to_numpy() == "Park") & (df["is_weekend"].to_numpy() == 1),
        5.5,
        0.0,
    )
    school_weekday_boost = np.where(
        (df["district"].to_numpy() == "School Zone") & (df["is_weekend"].to_numpy() == 0),
        3.0,
        0.0,
    )
    rain_slowdown = np.where(
        (df["rain_flag"].to_numpy() == 1)
        & np.isin(df["district"].to_numpy(), ["Park", "Market"]),
        -3.8,
        0.0,
    )
    warm_organic_boost = np.where(
        (df["waste_type"].to_numpy() == "Organic") & (df["temperature"].to_numpy() > 22),
        2.8,
        0.0,
    )

    increment = (
        hours_factor * daily_rate * district_factor * waste_factor
        + activity_score * 1.12
        + weekend_effect
        + park_weekend_boost
        + school_weekday_boost
        + rain_slowdown
        + warm_organic_boost
    )
    noise = rng.normal(0, 4.5, size=len(df))
    target = df["previous_fill_pct"].to_numpy() * 0.58 + increment + noise

    return np.round(np.clip(target, 0, 100), 2)


def generate_training_data(n_rows: int = 4500, seed: int = 42) -> pd.DataFrame:
    """Generate and save realistic synthetic historical smart-bin observations."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df = _generate_common_features(n_rows=n_rows, seed=seed, bin_prefix="TRN")
    df["target_fill_pct"] = _target_fill_pct(df, seed=seed)
    df.to_csv(TRAINING_DATA_PATH, index=False)
    return df


def generate_current_bins(n_bins: int = 180, seed: int = 42) -> pd.DataFrame:
    """Generate and save the current smart-bin snapshot used by the dashboard."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df = _generate_common_features(n_rows=n_bins, seed=seed + 1, bin_prefix="BIN")
    district_order = {district: index for index, district in enumerate(DISTRICTS)}
    df = (
        df.assign(_district_order=df["district"].map(district_order))
        .sort_values(["_district_order", "longitude", "latitude"], ascending=True)
        .drop(columns=["_district_order"])
        .reset_index(drop=True)
    )
    df["bin_id"] = [f"BIN-{i:04d}" for i in range(1, n_bins + 1)]
    df.to_csv(BINS_PATH, index=False)
    return df


def ensure_data_exists() -> None:
    """Create the demo data files if they are missing."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TRAINING_DATA_PATH.exists():
        generate_training_data()
    if not BINS_PATH.exists():
        generate_current_bins()
