from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from src.data_generator import ASTANA_LATITUDE, ASTANA_LONGITUDE
from src.optimize.distances import Point, get_route_geometry
from src.optimize.solver import Plan

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
        priority_df = (
            all_bins_df[all_bins_df["priority"] == priority] if not all_bins_df.empty else all_bins_df
        )
        if priority_df.empty:
            continue

        if priority == "Skip":
            marker_size = (priority_df["predicted_fill_pct"].clip(10, 75) / 14 + 3.5).tolist()
            opacity = 0.24
        else:
            marker_size = (priority_df["predicted_fill_pct"].clip(35, 100) / 8.5 + 5).tolist()
            opacity = 0.9

        fig.add_trace(
            go.Scattermap(
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
        go.Scattermap(
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
        go.Scattermap(
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
        go.Scattermap(
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
        map={
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


def create_simulation_map(
    world_df: pd.DataFrame,
    plan: Plan,
    depot: Point,
    landfill: Point,
    lang: str = "ru",
    *,
    selected_truck_id: str | None = None,
    depot_assumed: bool = True,
) -> tuple[go.Figure, str, int]:
    """V2 class map with road-following truck overlays and an offline fallback."""
    fig = go.Figure()
    class_colors = {"GREEN": "#16a34a", "YELLOW": "#eab308", "RED": "#dc2626"}
    for klass in ("GREEN", "YELLOW", "RED"):
        sites = world_df[world_df["klass"] == klass]
        if sites.empty:
            continue
        sizes = 7 + 13 * (sites["capacity_liters"].astype(float) / world_df["capacity_liters"].max()) ** 0.5
        labels = (
            ("Заполнение", "ПЕРЕПОЛНЕНИЕ", "Темп", "Дней после вывоза", "Класс", "Причина")
            if lang == "ru"
            else ("Fill", "OVERFLOW", "Rate", "Days since service", "Class", "Reason")
        )
        reason_labels = {
            "max_interval": "максимальный интервал" if lang == "ru" else "maximum interval",
            "high_fill": "высокое заполнение" if lang == "ru" else "high fill",
            "overflow_predicted": "прогноз переполнения" if lang == "ru" else "overflow predicted",
            "none": "нет" if lang == "ru" else "none",
        }
        hover = [
            "<br>".join(
                [
                    f"<b>{row.address}</b>",
                    f"{labels[0]}: {min(float(row.fill_pct), 100):.1f}%"
                    + (f" · {labels[1]}" if float(row.fill_pct) > 100 else ""),
                    f"{labels[2]}: {float(row.daily_fill_rate_pct):.1f}%/"
                    + ("день" if lang == "ru" else "day"),
                    f"{labels[3]}: {int(row.days_since_service)}",
                    f"{labels[4]}: {row.klass}",
                    f"{labels[5]}: {reason_labels.get(row.reason, row.reason)}",
                ]
            )
            for row in sites.itertuples()
        ]
        fig.add_trace(
            go.Scattermap(
                lat=sites["lat"],
                lon=sites["lon"],
                mode="markers",
                marker={"size": sizes, "color": class_colors[klass], "opacity": 0.82},
                text=hover,
                hoverinfo="text",
                name=klass,
            )
        )

    by_id = world_df.set_index("site_id")
    geometry_sources: list[str] = []
    routes = [
        route for route in plan.routes if selected_truck_id is None or route.truck_id == selected_truck_id
    ]
    for index, route in enumerate(routes):
        ordered_points: list[Point] = [depot]
        for stop in route.ordered_stops:
            if stop == "LANDFILL":
                ordered_points.append(landfill)
            elif stop in by_id.index:
                row = by_id.loc[stop]
                ordered_points.append((float(row["lat"]), float(row["lon"])))
        ordered_points.append(depot)
        geometry = get_route_geometry(ordered_points)
        color = TRUCK_ROUTE_COLORS[index % len(TRUCK_ROUTE_COLORS)]
        for segment_index, segment in enumerate(geometry.segments):
            geometry_sources.append(segment.source)
            is_final = segment_index == len(geometry.segments) - 1
            is_to_landfill = segment_index == len(geometry.segments) - 2
            if is_final:
                label = (
                    f"{route.truck_id} · полигон → парк · {segment.distance_m / 1000:.1f} км"
                    if lang == "ru"
                    else f"{route.truck_id} · landfill → depot · {segment.distance_m / 1000:.1f} km"
                )
            elif is_to_landfill:
                label = (
                    f"{route.truck_id} · → полигон · {segment.distance_m / 1000:.1f} км"
                    if lang == "ru"
                    else f"{route.truck_id} · → landfill · {segment.distance_m / 1000:.1f} km"
                )
            else:
                label = route.truck_id
            if segment.source == "straight":
                latitudes: list[float | None] = []
                longitudes: list[float | None] = []
                pieces = 24
                for piece in range(0, pieces, 2):
                    for fraction in (piece / pieces, (piece + 1) / pieces):
                        latitudes.append(segment.start[0] + (segment.end[0] - segment.start[0]) * fraction)
                        longitudes.append(segment.start[1] + (segment.end[1] - segment.start[1]) * fraction)
                    latitudes.append(None)
                    longitudes.append(None)
            else:
                latitudes = [point[0] for point in segment.points]
                longitudes = [point[1] for point in segment.points]
            fig.add_trace(
                go.Scattermap(
                    lat=latitudes,
                    lon=longitudes,
                    mode="lines",
                    line={"width": 5, "color": color},
                    name=label,
                    hoverinfo="name",
                    showlegend=is_final or is_to_landfill or segment_index == 0,
                )
            )

    infrastructure = (
        (
            depot,
            "#2563eb",
            (
                "Парк (предположительно)"
                if lang == "ru" and depot_assumed
                else "Depot (assumed)"
                if depot_assumed
                else "Парк"
                if lang == "ru"
                else "Depot"
            ),
        ),
        (landfill, "#7c2d12", "Полигон" if lang == "ru" else "Landfill"),
    )
    for point, color, label in infrastructure:
        fig.add_trace(
            go.Scattermap(
                lat=[point[0]],
                lon=[point[1]],
                mode="markers",
                marker={"size": 19, "color": color},
                hovertext=[label],
                hoverinfo="text",
                name=label,
            )
        )
    fig.update_layout(
        map={
            "style": "carto-positron",
            "center": {"lat": float(world_df["lat"].mean()), "lon": float(world_df["lon"].mean())},
            "zoom": 11.2,
        },
        height=720,
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        legend={"orientation": "h", "y": 1.02, "x": 0},
    )
    source_set = set(geometry_sources)
    source = next(iter(source_set)) if len(source_set) == 1 else "mixed" if source_set else "straight"
    return fig, source, geometry_sources.count("straight")
