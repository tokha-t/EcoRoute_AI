from __future__ import annotations

import pandas as pd

from src.config import (
    AVERAGE_TRUCK_SPEED_KMH,
    CO2_KG_PER_LITER_DIESEL,
    FUEL_CONSUMPTION_LITERS_PER_KM,
    FUEL_COST_KZT_PER_LITER,
    STOP_TIME_MINUTES_PER_BIN,
)


def calculate_savings(
    all_bins_df: pd.DataFrame,
    selected_bins_df: pd.DataFrame,
    route_comparison: dict,
) -> dict:
    total_bins = int(len(all_bins_df))
    selected_bins = int(len(selected_bins_df))
    bins_skipped = max(0, total_bins - selected_bins)
    skipped_percent = (bins_skipped / total_bins * 100) if total_bins else 0.0

    fixed_route_distance = max(0.0, float(route_comparison["fixed_route_distance_km"]))
    optimized_route_distance = max(0.0, float(route_comparison["selected_optimized_distance_km"]))
    distance_saved = max(0.0, fixed_route_distance - optimized_route_distance)
    distance_saved_percent = (distance_saved / fixed_route_distance * 100) if fixed_route_distance else 0.0

    driving_time_saved = distance_saved / AVERAGE_TRUCK_SPEED_KMH * 60
    stop_time_saved = bins_skipped * STOP_TIME_MINUTES_PER_BIN
    total_time_saved = driving_time_saved + stop_time_saved
    fuel_saved = distance_saved * FUEL_CONSUMPTION_LITERS_PER_KM
    co2_saved = fuel_saved * CO2_KG_PER_LITER_DIESEL
    fuel_cost_saved = fuel_saved * FUEL_COST_KZT_PER_LITER

    return {
        "total_bins": total_bins,
        "selected_bins": selected_bins,
        "bins_skipped": bins_skipped,
        "skipped_percent": round(skipped_percent, 2),
        "fixed_route_distance_km": round(fixed_route_distance, 2),
        "optimized_route_distance_km": round(optimized_route_distance, 2),
        "distance_saved_km": round(distance_saved, 2),
        "distance_saved_percent": round(distance_saved_percent, 2),
        "estimated_driving_time_saved_minutes": round(driving_time_saved, 1),
        "estimated_stop_time_saved_minutes": round(stop_time_saved, 1),
        "estimated_total_time_saved_minutes": round(total_time_saved, 1),
        "estimated_fuel_saved_liters": round(fuel_saved, 2),
        "estimated_co2_saved_kg": round(co2_saved, 2),
        "estimated_fuel_cost_saved_kzt": round(fuel_cost_saved, 0),
    }

