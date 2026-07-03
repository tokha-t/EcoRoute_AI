from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from src.data_generator import ASTANA_LATITUDE, ASTANA_LONGITUDE

PRIORITY_COLORS = {
    "Critical": "#dc2626",
    "High": "#f97316",
    "Medium": "#eab308",
    "Skip": "#cbd5e1",
    "Depot": "#2563eb",
    "Route": "#0f172a",
    "Order": "#0f766e",
}

TRUCK_ROUTE_COLORS = ["#0f172a", "#7c3aed", "#0d9488"]
MAX_LABELED_STOPS = 24


def priority_color(priority: str) -> str:
    return PRIORITY_COLORS.get(priority, PRIORITY_COLORS["Skip"])


def build_hover_text(row) -> str:
    predicted = row.get("predicted_fill_pct", 0)
    capacity = row.get("capacity_liters", "Unknown")
    activity = row.get("nearby_activity_score", "Unknown")
    return (
        f"<b>{row.get('bin_id', 'Unknown')}</b><br>"
        f"District: {row.get('district', 'Unknown')}<br>"
        f"Waste type: {row.get('waste_type', 'Unknown')}<br>"
        f"Predicted fill: {predicted:.1f}%<br>"
        f"Priority: {row.get('priority', 'Skip')}<br>"
        f"Capacity: {capacity} L<br>"
        f"Activity score: {activity}"
    )


def _add_bin_markers(fig: go.Figure, all_bins_df: pd.DataFrame) -> None:
    for priority in ["Skip", "Medium", "High", "Critical"]:
        priority_df = all_bins_df[all_bins_df["priority"] == priority] if not all_bins_df.empty else all_bins_df
        if priority_df.empty:
            continue

        if priority == "Skip":
            marker_size = (priority_df["predicted_fill_pct"].clip(10, 75) / 14 + 3.5).tolist()
            opacity = 0.24
        else:
            marker_size = (priority_df["predicted_fill_pct"].clip(35, 100) / 8.5 + 5).tolist()
            opacity = 0.9

        fig.add_trace(
            go.Scattermapbox(
                lat=priority_df["latitude"],
                lon=priority_df["longitude"],
                mode="markers",
                marker={
                    "size": marker_size,
                    "color": priority_color(priority),
                    "opacity": opacity,
                },
                name=f"{priority} bins",
                text=[build_hover_text(row) for _, row in priority_df.iterrows()],
                hoverinfo="text",
                showlegend=False,
            )
        )


def _add_route_traces(
    fig: go.Figure,
    route_points: list[dict],
    line_color: str,
    marker_color: str,
    show_labels: bool,
    route_name: str,
) -> None:
    if len(route_points) < 2:
        return

    fig.add_trace(
        go.Scattermapbox(
            lat=[point["latitude"] for point in route_points],
            lon=[point["longitude"] for point in route_points],
            mode="lines",
            line={"width": 5, "color": line_color},
            name=route_name,
            hoverinfo="skip",
            showlegend=False,
        )
    )

    stop_points = [point for point in route_points[1:-1]]
    fig.add_trace(
        go.Scattermapbox(
            lat=[point["latitude"] for point in stop_points],
            lon=[point["longitude"] for point in stop_points],
            mode="markers+text" if show_labels else "markers",
            marker={
                "size": 9,
                "color": marker_color,
                "opacity": 0.86,
            },
            text=[str(index) for index, _ in enumerate(stop_points, start=1)] if show_labels else None,
            textposition="top center",
            textfont={"size": 10, "color": "#0f172a"},
            name=f"{route_name} order",
            hovertext=[
                f"{route_name} · Stop {index}: {point.get('bin_id', 'Unknown')}<br>"
                f"{point.get('district', 'Unknown')} · {point.get('priority', 'Unknown')}"
                for index, point in enumerate(stop_points, start=1)
            ],
            hoverinfo="text",
            showlegend=False,
        )
    )


def _add_depot_and_layout(fig: go.Figure, all_bins_df: pd.DataFrame, depot: dict) -> None:
    map_center = {"lat": ASTANA_LATITUDE, "lon": ASTANA_LONGITUDE}
    if not all_bins_df.empty:
        map_center = {
            "lat": float(all_bins_df["latitude"].mean()),
            "lon": float(all_bins_df["longitude"].mean()),
        }
    map_zoom = 11.05 if len(all_bins_df) > 120 else 11.35 if len(all_bins_df) > 75 else 12.0

    fig.add_trace(
        go.Scattermapbox(
            lat=[depot["latitude"]],
            lon=[depot["longitude"]],
            mode="markers",
            marker={
                "size": 17,
                "color": PRIORITY_COLORS["Depot"],
            },
            name="Depot",
            text=["Depot"],
            hoverinfo="text",
            showlegend=False,
        )
    )

    fig.update_layout(
        mapbox={
            "style": "carto-positron",
            "center": map_center,
            "zoom": map_zoom,
        },
        height=720,
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        showlegend=False,
    )


def create_route_map(
    all_bins_df: pd.DataFrame,
    selected_bins_df: pd.DataFrame,
    route_points: list[dict],
    depot: dict,
    threshold: int | float,
) -> go.Figure:
    fig = go.Figure()
    _add_bin_markers(fig, all_bins_df)
    _add_route_traces(
        fig,
        route_points,
        line_color=PRIORITY_COLORS["Route"],
        marker_color=PRIORITY_COLORS["Order"],
        show_labels=len(selected_bins_df) <= MAX_LABELED_STOPS,
        route_name="Optimized route",
    )
    _add_depot_and_layout(fig, all_bins_df, depot)
    return fig


def create_fleet_route_map(
    all_bins_df: pd.DataFrame,
    selected_bins_df: pd.DataFrame,
    truck_routes: list[list[dict]],
    depot: dict,
    threshold: int | float,
) -> go.Figure:
    """Map with one colored route line per truck (CVRP engine)."""
    fig = go.Figure()
    _add_bin_markers(fig, all_bins_df)
    total_stops = sum(max(0, len(route_points) - 2) for route_points in truck_routes)
    for index, route_points in enumerate(truck_routes):
        color = TRUCK_ROUTE_COLORS[index % len(TRUCK_ROUTE_COLORS)]
        _add_route_traces(
            fig,
            route_points,
            line_color=color,
            marker_color=color,
            show_labels=total_stops <= MAX_LABELED_STOPS,
            route_name=f"Truck {index + 1}",
        )
    _add_depot_and_layout(fig, all_bins_df, depot)
    return fig
