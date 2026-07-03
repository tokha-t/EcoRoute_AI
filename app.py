from __future__ import annotations

import json
import tempfile
from pathlib import Path

import joblib
import pandas as pd
import plotly.express as px
import requests
import streamlit as st

from src.app_logic import (
    MODE_LIVE_PHOTO,
    MODE_SIMULATION,
    MODES,
    SAVINGS_TARGET_TEXT,
    SIMULATED_BANNER_TEXT,
    confirmed_observations,
    merge_observations,
    observation_rows,
    observations_to_plan_sites,
    show_simulated_banner,
)
from src.data_generator import (
    ASTANA_LATITUDE,
    ASTANA_LONGITUDE,
    BINS_PATH,
    DISTRICTS,
    WASTE_TYPES,
    ensure_data_exists,
)
from src.map_utils import create_fleet_route_map, create_route_map
from src.optimize.distances import apply_road_distances
from src.optimize.solver import (
    DEFAULT_TRUCK_CAPACITY_KG,
    MAX_TRUCKS,
    MIN_TRUCKS,
    Plan,
    SolverParams,
    Truck,
    plan_routes,
)
from src.photo_fill.estimator import (
    FILL_CLASSES,
    UNCERTAIN,
    EstimationError,
    api_key_available,
    estimate_fill,
)
from src.predict import assign_priority, predict_fill_levels
from src.routing import compare_routes
from src.savings import calculate_savings
from src.train_model import METRICS_PATH, MODEL_PATH, train_and_save_model

DEPOT = {"latitude": ASTANA_LATITUDE, "longitude": ASTANA_LONGITUDE}

ENGINE_CVRP = "CVRP (OR-Tools)"
ENGINE_CLASSIC = "Classic (2-opt)"
DEFAULT_SHIFT_HOURS = 8

MODE_TOGGLE_KEY = "mode_toggle"
SESSION_PHOTO_ESTIMATES = "photo_estimates"
SESSION_LIVE_PLAN = "live_plan_observations"
UPLOAD_TYPES = ["jpg", "jpeg", "png"]

STYLES_PATH = Path(__file__).parent / "assets" / "styles.css"
PHOTO_EVAL_REPORT_PATH = Path(__file__).parent / "reports" / "photo_eval.md"

PRIORITY_COLORS = {
    "Critical": "#dc2626",
    "High": "#f97316",
    "Medium": "#eab308",
    "Skip": "#cbd5e1",
}

CHART_COLORS = {
    "fixed": "#334155",
    "greedy": "#10b981",
    "optimized": "#0ea5e9",
    "blue": "#0ea5e9",
    "green": "#10b981",
    "surface": "#ffffff",
    "surface_2": "#f8fafc",
    "grid": "#e2e8f0",
    "axis": "#cbd5e1",
    "text": "#0f172a",
}

PLOTLY_CONFIG = {
    "displayModeBar": False,
    "responsive": True,
}


st.set_page_config(
    page_title="EcoRoute AI",
    page_icon="♻️",
    layout="wide",
    initial_sidebar_state="auto",
)


def inject_styles() -> None:
    css = STYLES_PATH.read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def render_simulated_banner() -> None:
    """Honesty invariant: simulated savings/metrics always carry this banner."""
    if show_simulated_banner(st.session_state.get(MODE_TOGGLE_KEY, MODE_SIMULATION)):
        st.markdown(
            f'<div class="sim-banner">{SIMULATED_BANNER_TEXT}</div>',
            unsafe_allow_html=True,
        )


def apply_chart_theme(fig, height: int = 360):
    fig.update_layout(
        template="plotly_white",
        height=height,
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        font={
            "family": "Inter, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
            "size": 12,
            "color": CHART_COLORS["text"],
        },
        title={
            "font": {"size": 17, "color": "#172033"},
            "x": 0.02,
            "xanchor": "left",
        },
        margin={"l": 44, "r": 28, "t": 58, "b": 46},
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "left",
            "x": 0,
            "font": {"size": 11},
        },
    )
    fig.update_xaxes(
        showgrid=True,
        gridcolor=CHART_COLORS["grid"],
        zeroline=False,
        linecolor=CHART_COLORS["axis"],
        tickfont={"color": "#667085"},
        title_font={"color": "#475467"},
    )
    fig.update_yaxes(
        showgrid=False,
        zeroline=False,
        linecolor=CHART_COLORS["axis"],
        tickfont={"color": "#667085"},
        title_font={"color": "#475467"},
    )
    return fig


def stretch_plotly_chart(fig, config: dict | None = None) -> None:
    chart_config = {**PLOTLY_CONFIG, **(config or {})}
    try:
        st.plotly_chart(fig, width="stretch", config=chart_config)
    except TypeError:
        st.plotly_chart(fig, use_container_width=True, config=chart_config)


def stretch_dataframe(df: pd.DataFrame, **kwargs) -> None:
    try:
        st.dataframe(df, width="stretch", **kwargs)
    except TypeError:
        st.dataframe(df, use_container_width=True, **kwargs)


def stretch_download_button(label: str, **kwargs) -> bool:
    try:
        return st.download_button(label, width="stretch", **kwargs)
    except TypeError:
        return st.download_button(label, use_container_width=True, **kwargs)


@st.cache_data(show_spinner=False)
def load_bins() -> pd.DataFrame:
    return pd.read_csv(BINS_PATH)


def load_metrics() -> dict:
    if METRICS_PATH.exists():
        try:
            return json.loads(METRICS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def model_feature_importance_chart():
    try:
        model = joblib.load(MODEL_PATH)
        preprocessor = model.named_steps["preprocessor"]
        regressor = model.named_steps["model"]
        feature_names = preprocessor.get_feature_names_out()
        importances = regressor.feature_importances_
        importance_df = (
            pd.DataFrame({"feature": feature_names, "importance": importances})
            .assign(
                feature=lambda df: df["feature"]
                .str.replace("categorical__", "", regex=False)
                .str.replace("numeric__", "", regex=False)
            )
            .sort_values("importance", ascending=False)
            .head(10)
            .sort_values("importance", ascending=True)
        )
        fig = px.bar(
            importance_df,
            x="importance",
            y="feature",
            orientation="h",
            title="Top 10 model features",
            labels={"importance": "Importance", "feature": "Feature"},
            color_discrete_sequence=[CHART_COLORS["blue"]],
        )
        fig.update_traces(
            marker_line_width=0,
            hovertemplate="%{y}<br>Importance %{x:.3f}<extra></extra>",
        )
        return apply_chart_theme(fig, height=420)
    except Exception as exc:
        st.info(f"Feature importance is unavailable right now: {exc}")
        return None


def render_kpis(savings: dict, predicted_df: pd.DataFrame) -> None:
    render_simulated_banner()
    avg_fill = predicted_df["predicted_fill_pct"].mean() if not predicted_df.empty else 0
    cost_saved = savings["estimated_fuel_cost_saved_kzt"]
    cost_label = f"{cost_saved / 1000:.1f}k KZT" if cost_saved >= 1000 else f"{cost_saved:,.0f} KZT"
    kpis = [
        ("Total bins", f"{savings['total_bins']:,}", None),
        ("Bins selected", f"{savings['selected_bins']:,}", None),
        ("Bins skipped", f"{savings['bins_skipped']:,}", f"{savings['skipped_percent']:.1f}%"),
        ("Avg predicted fill", f"{avg_fill:.1f}%", None),
        ("Optimized route", f"{savings['optimized_route_distance_km']:.1f} km", None),
        ("Distance saved", f"{savings['distance_saved_km']:.1f} km", f"{savings['distance_saved_percent']:.1f}%"),
        ("Time saved", f"{savings['estimated_total_time_saved_minutes']:.0f} min", None),
        ("Fuel saved", f"{savings['estimated_fuel_saved_liters']:.1f} L", None),
        ("CO₂ saved", f"{savings['estimated_co2_saved_kg']:.1f} kg", None),
        ("Cost saved", cost_label, None),
    ]

    for start in range(0, len(kpis), 4):
        columns = st.columns(4)
        for column, (label, value, delta) in zip(columns, kpis[start : start + 4]):
            column.metric(label, value, delta=delta)
    st.caption(SAVINGS_TARGET_TEXT)


def render_recommendation(selected_bins_df: pd.DataFrame, predicted_df: pd.DataFrame, savings: dict) -> None:
    if selected_bins_df.empty:
        st.info(
            "No bins exceed the current threshold. The city can skip this collection cycle or lower the threshold."
        )
        return

    top_districts = selected_bins_df["district"].value_counts().head(2).index.tolist()
    district_phrase = " and ".join(top_districts) if top_districts else "the filtered area"
    recommendation = (
        f"Today, EcoRoute AI recommends collecting {len(selected_bins_df)} out of {len(predicted_df)} bins, "
        f"prioritizing {district_phrase} districts. Estimated savings (simulated): "
        f"{savings['distance_saved_km']:.1f} km, "
        f"{savings['estimated_total_time_saved_minutes']:.0f} minutes, "
        f"{savings['estimated_fuel_saved_liters']:.1f} liters of fuel, "
        f"{savings['estimated_co2_saved_kg']:.1f} kg CO₂, and "
        f"{savings['estimated_fuel_cost_saved_kzt']:,.0f} KZT."
    )

    if (selected_bins_df["priority"] == "Critical").any():
        st.warning(recommendation)
    else:
        st.success(recommendation)


def render_critical_alert(selected_bins_df: pd.DataFrame) -> None:
    if selected_bins_df.empty or "priority" not in selected_bins_df.columns:
        return

    critical_counts = selected_bins_df[selected_bins_df["priority"] == "Critical"]["district"].value_counts()
    if critical_counts.empty:
        return

    top_district = critical_counts.index[0]
    top_count = int(critical_counts.iloc[0])
    if top_count >= 5:
        st.warning(
            f"Alert: {top_district} district has {top_count} critical bins and should be prioritized today."
        )


def render_hero(threshold: int | float, district_filter: str, waste_type_filter: str) -> None:
    district_label = district_filter if district_filter != "All" else "All districts"
    waste_label = waste_type_filter if waste_type_filter != "All" else "All waste streams"
    st.markdown(
        f"""
        <section class="eco-hero">
            <div class="eco-hero-content">
                <div class="eco-eyebrow">SmartScape Hackathon · Ecology & Urban Environment · Simulation mode</div>
                <h1>EcoRoute AI</h1>
                <p>
                    Predict near-full bins, prioritize the right stops, and build a cleaner truck route
                    for one Astana dispatch zone before the shift starts.
                </p>
            </div>
            <div class="hero-stats">
                <div class="hero-stat">
                    <div class="hero-stat-value">{threshold}%</div>
                    <div class="hero-stat-label">collection threshold</div>
                </div>
                <div class="hero-stat">
                    <div class="hero-stat-value">{district_label}</div>
                    <div class="hero-stat-label">district filter</div>
                </div>
                <div class="hero-stat">
                    <div class="hero-stat-value">{waste_label}</div>
                    <div class="hero-stat-label">waste filter</div>
                </div>
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def render_pipeline(threshold: int | float, engine: str) -> None:
    if engine == ENGINE_CVRP:
        optimize_text = "OR-Tools CVRP splits stops across trucks within capacity and shift limits."
    else:
        optimize_text = "Nearest-neighbor route is improved with 2-opt."
    st.markdown(
        f"""
        <div class="eco-track">Live operations pipeline · Astana demo</div>
        <div class="eco-pipeline">
            <div class="eco-step">
                <strong>1. Predict</strong>
                <span>RandomForestRegressor forecasts bin fill percentage.</span>
            </div>
            <div class="eco-step">
                <strong>2. Prioritize</strong>
                <span>Bins at or above {threshold}% enter today's collection plan.</span>
            </div>
            <div class="eco-step">
                <strong>3. Optimize</strong>
                <span>{optimize_text}</span>
            </div>
            <div class="eco-step">
                <strong>4. Quantify</strong>
                <span>Savings are simulated in this demo; real-world deployments target 15–25%.</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def scenario_metrics(predicted_df: pd.DataFrame, scenario_threshold: int) -> dict:
    scenario_df = assign_priority(predicted_df, threshold=scenario_threshold)
    scenario_selected_df = scenario_df[scenario_df["must_serve"]].copy()
    scenario_routes = apply_road_distances(compare_routes(scenario_df, scenario_selected_df, DEPOT))
    return calculate_savings(scenario_df, scenario_selected_df, scenario_routes)


@st.cache_data(show_spinner=False)
def solve_cvrp(selected_bins_df: pd.DataFrame, n_trucks: int, capacity_kg: float, shift_hours: float) -> Plan:
    trucks = [Truck(truck_id=f"Truck {i}", capacity_kg=capacity_kg) for i in range(1, n_trucks + 1)]
    params = SolverParams(
        depot=(DEPOT["latitude"], DEPOT["longitude"]),
        shift_duration_s=shift_hours * 3600.0,
    )
    return plan_routes(selected_bins_df, trucks, params)


def depot_route_point() -> dict:
    return {
        "bin_id": "Depot",
        "latitude": DEPOT["latitude"],
        "longitude": DEPOT["longitude"],
        "district": "Depot",
        "priority": "Depot",
    }


def plan_truck_route_points(plan: Plan, sites_df: pd.DataFrame) -> list[list[dict]]:
    lookup = sites_df.set_index("bin_id")
    truck_routes = []
    for route in plan.routes:
        points = [depot_route_point()]
        for site_id in route.site_ids:
            row = lookup.loc[site_id]
            points.append(
                {
                    "bin_id": site_id,
                    "latitude": float(row["latitude"]),
                    "longitude": float(row["longitude"]),
                    "district": row.get("district", ""),
                    "priority": row.get("priority", ""),
                }
            )
        points.append(depot_route_point())
        truck_routes.append(points)
    return truck_routes


def cvrp_route_order_df(plan: Plan, sites_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for route, points in zip(plan.routes, plan_truck_route_points(plan, sites_df)):
        for order, point in enumerate(points, start=1):
            rows.append(
                {
                    "truck": route.truck_id,
                    "stop_order": order,
                    "bin_id": point["bin_id"],
                    "district": point["district"],
                    "priority": point["priority"],
                    "latitude": point["latitude"],
                    "longitude": point["longitude"],
                }
            )
    return pd.DataFrame(rows)


def render_cvrp_violations(plan: Plan) -> None:
    bullets = "\n".join(f"- {violation}" for violation in plan.violations)
    st.error(
        "CVRP plan is infeasible, showing the Classic route instead:\n"
        f"{bullets}\n\n"
        "Add a truck, raise capacity, or extend the shift in the sidebar."
    )


def render_engine_comparison(route_comparison: dict, plan: Plan, n_trucks: int, capacity_kg: float) -> None:
    st.subheader("Engine comparison")
    render_simulated_banner()
    classic_km = float(route_comparison["selected_optimized_distance_km"])
    if plan.violations:
        st.warning(
            "CVRP found no feasible plan for the current fleet, so only the Classic "
            f"single-loop result ({classic_km:.1f} km) is available."
        )
        return

    cvrp_km = plan.total_distance_m / 1000.0
    delta_km = classic_km - cvrp_km
    delta_pct = (delta_km / classic_km * 100.0) if classic_km else 0.0
    col_classic, col_cvrp, col_delta = st.columns(3)
    col_classic.metric("Classic (2-opt), one loop", f"{classic_km:.1f} km")
    col_cvrp.metric(f"CVRP (OR-Tools), {len(plan.routes)} truck(s)", f"{cvrp_km:.1f} km")
    col_delta.metric("CVRP vs Classic", f"{delta_km:+.1f} km", delta=f"{delta_pct:+.1f}%")

    truck_rows = [
        {
            "truck": route.truck_id,
            "stops": len(route.site_ids),
            "load (kg)": round(route.load_kg),
            "capacity (kg)": round(capacity_kg),
            "duration (min)": round(route.duration_s / 60),
            "distance (km)": round(route.distance_m / 1000, 1),
        }
        for route in plan.routes
    ]
    stretch_dataframe(pd.DataFrame(truck_rows), hide_index=True)

    caption = (
        "Both engines solve the same selected bins on the same distance matrix. "
        "Classic drives one unconstrained loop; CVRP splits stops across "
        f"{n_trucks} truck(s) with capacity and shift limits, so its total can be "
        "longer — that extra distance is the price of a physically executable plan."
    )
    if plan.dropped_site_ids:
        caption += (
            f" CVRP dropped {len(plan.dropped_site_ids)} optional bin(s) that did not "
            "fit the fleet's capacity or shift."
        )
    st.caption(caption)


def render_scenario_cards(predicted_df: pd.DataFrame) -> None:
    st.subheader("Scenario comparison")
    render_simulated_banner()
    st.caption("Threshold what-ifs use the Classic (2-opt) engine for fast recomputation.")
    scenarios = [
        ("Conservative", 85, "Collect only the most urgent bins."),
        ("Balanced", 75, "Default operating plan for the demo."),
        ("Aggressive", 65, "Collect earlier to reduce overflow risk."),
    ]
    columns = st.columns(3)

    for column, (name, scenario_threshold, note) in zip(columns, scenarios):
        metrics = scenario_metrics(predicted_df, scenario_threshold)
        with column:
            st.markdown(
                f"""
                <div class="scenario-card">
                    <h4>{name}</h4>
                    <div class="scenario-meta">{scenario_threshold}% threshold · {note}</div>
                    <div class="scenario-grid">
                        <div>
                            <div class="scenario-value">{metrics["selected_bins"]}</div>
                            <div class="scenario-label">bins selected</div>
                        </div>
                        <div>
                            <div class="scenario-value">{metrics["distance_saved_km"]:.1f}</div>
                            <div class="scenario-label">km saved</div>
                        </div>
                        <div>
                            <div class="scenario-value">{metrics["estimated_total_time_saved_minutes"]:.0f}</div>
                            <div class="scenario-label">minutes saved</div>
                        </div>
                        <div>
                            <div class="scenario-value">{metrics["estimated_co2_saved_kg"]:.1f}</div>
                            <div class="scenario-label">kg CO₂ saved</div>
                        </div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def render_map_context(
    predicted_df: pd.DataFrame,
    selected_bins_df: pd.DataFrame,
    savings: dict,
    route_comparison: dict,
    fourth_stat: tuple[str, str] | None = None,
) -> None:
    selected_percent = (len(selected_bins_df) / len(predicted_df) * 100) if len(predicted_df) else 0
    osrm_used = route_comparison.get("distance_source") == "osrm"
    badge_class = "distance-badge osrm" if osrm_used else "distance-badge fallback"
    badge_label = "road distances (OSRM)" if osrm_used else "straight-line est."
    if fourth_stat is None:
        fourth_stat = (
            f"{route_comparison['two_opt_improvement_km']:.1f} km",
            "2-opt route improvement",
        )
    fourth_stat_value, fourth_stat_label = fourth_stat
    render_simulated_banner()
    st.markdown(
        f"""
        <div class="map-panel">
            <h3>Optimized collection map <span class="{badge_class}">{badge_label}</span></h3>
            <p>
                Dense Astana dispatch view with {len(predicted_df)} bins. Skipped bins are intentionally faint,
                selected bins stay vivid, and the route is drawn above the map so the truck path remains readable.
            </p>
            <div class="map-legend">
                <span><i class="legend-line"></i>Optimized route</span>
                <span><i class="legend-dot legend-depot"></i>Depot</span>
                <span><i class="legend-dot legend-critical"></i>Critical</span>
                <span><i class="legend-dot legend-high"></i>High</span>
                <span><i class="legend-dot legend-medium"></i>Medium</span>
                <span><i class="legend-dot legend-skip"></i>Skipped</span>
            </div>
            <div class="map-stat-grid">
                <div class="map-stat">
                    <div class="map-stat-value">{savings["fixed_route_distance_km"]:.1f} km</div>
                    <div class="map-stat-label">fixed all-bin route</div>
                </div>
                <div class="map-stat">
                    <div class="map-stat-value">{savings["optimized_route_distance_km"]:.1f} km</div>
                    <div class="map-stat-label">optimized selected route</div>
                </div>
                <div class="map-stat">
                    <div class="map-stat-value">{selected_percent:.0f}%</div>
                    <div class="map-stat-label">bins selected today</div>
                </div>
                <div class="map-stat">
                    <div class="map-stat-value">{fourth_stat_value}</div>
                    <div class="map-stat-label">{fourth_stat_label}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def route_points_to_dataframe(route_points: list[dict]) -> pd.DataFrame:
    rows = []
    for index, point in enumerate(route_points, start=1):
        rows.append(
            {
                "stop_order": index,
                "bin_id": point.get("bin_id", "Depot"),
                "district": point.get("district", "Depot"),
                "priority": point.get("priority", "Depot"),
                "latitude": point.get("latitude"),
                "longitude": point.get("longitude"),
            }
        )
    return pd.DataFrame(rows)


def estimate_uploaded_photo(upload) -> dict:
    """Run the live estimator on one uploaded photo; errors become table-safe dicts."""
    suffix = Path(upload.name).suffix or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(upload.getvalue())
        temp_path = Path(handle.name)
    try:
        result = estimate_fill(temp_path)
        return {
            "photo": upload.name,
            "cls": result["cls"],
            "confidence": result["confidence"],
        }
    except (EstimationError, requests.RequestException) as exc:
        return {"photo": upload.name, "error": str(exc)}
    finally:
        temp_path.unlink(missing_ok=True)


def render_photo_eval_page() -> None:
    st.subheader("Photo model evaluation")
    if PHOTO_EVAL_REPORT_PATH.exists():
        st.markdown(PHOTO_EVAL_REPORT_PATH.read_text(encoding="utf-8"))
    else:
        st.info(
            "No evaluation report yet. Collect labeled field photos "
            "(`data/photos/` + `labels.csv`), then run:\n\n"
            "```\npython -m src.photo_fill.evaluate\n```\n\n"
            "Accuracy, macro-F1, and the per-class confusion matrix "
            "(by-site split, SPEC_V1 6.2) render here."
        )


def render_photo_estimates_table(current: list[dict], known_site_ids: list[str]) -> pd.DataFrame:
    st.subheader("Photo estimates")
    st.caption(
        "Rows marked ⚠️ are below the confidence threshold and excluded by default — "
        "check the photo yourself and set the class to include them. Assign a site "
        "to every row you want in the plan."
    )
    edited = st.data_editor(
        observation_rows(current, known_site_ids),
        key="photo_observations_editor",
        hide_index=True,
        column_config={
            "photo": st.column_config.TextColumn("photo", disabled=True),
            "site_id": st.column_config.SelectboxColumn("site", options=known_site_ids),
            "cls": st.column_config.SelectboxColumn(
                "class", options=list(FILL_CLASSES) + [UNCERTAIN]
            ),
            "confidence": st.column_config.NumberColumn(
                "confidence", disabled=True, format="%.2f"
            ),
            "flag": st.column_config.TextColumn("status", disabled=True),
            "include": st.column_config.CheckboxColumn("include"),
        },
    )
    return edited


def render_live_plan(
    bins_df: pd.DataFrame, n_trucks: int, truck_capacity_kg: float, shift_hours: float
) -> None:
    st.subheader("Today's plan")
    plan_observations = st.session_state.get(SESSION_LIVE_PLAN)
    if plan_observations is None or plan_observations.empty:
        st.info(
            "No sites in today's plan yet. Upload photos, review the table, "
            'then click "Add to today\'s plan".'
        )
        return

    sites = observations_to_plan_sites(bins_df, plan_observations)
    with st.spinner("Recomputing CVRP routes..."):
        plan = solve_cvrp(sites, n_trucks, truck_capacity_kg, shift_hours)

    if plan.violations:
        bullets = "\n".join(f"- {violation}" for violation in plan.violations)
        st.error(
            "CVRP plan is infeasible for the current fleet:\n"
            f"{bullets}\n\n"
            "Add a truck, raise capacity, or extend the shift in the sidebar."
        )
    else:
        distance_label = (
            "road distances (OSRM)"
            if plan.distance_source == "osrm"
            else "straight-line estimate (OSRM offline)"
        )
        col_sites, col_distance, col_trucks = st.columns(3)
        col_sites.metric("Sites in plan", len(sites))
        col_distance.metric("Planned distance", f"{plan.total_distance_m / 1000:.1f} km")
        col_trucks.metric("Trucks routed", f"{len(plan.routes)}/{n_trucks}")
        st.caption(
            f"Distance source: {distance_label}. Fill levels come from live photos; "
            "site coordinates come from the demo registry."
        )
        map_figure = create_fleet_route_map(
            sites, sites, plan_truck_route_points(plan, sites), DEPOT, threshold=0
        )
        stretch_plotly_chart(map_figure)
        stretch_dataframe(
            sites[
                [
                    "bin_id",
                    "district",
                    "photo_class",
                    "photo_confidence",
                    "predicted_fill_pct",
                    "priority",
                ]
            ],
            hide_index=True,
        )
        with st.expander("Truck route order"):
            stretch_dataframe(cvrp_route_order_df(plan, sites), hide_index=True)

    if st.button("Clear today's plan"):
        st.session_state.pop(SESSION_LIVE_PLAN, None)
        st.rerun()


def render_photo_plan_tab(
    bins_df: pd.DataFrame, n_trucks: int, truck_capacity_kg: float, shift_hours: float
) -> None:
    if not api_key_available():
        st.warning(
            "**API key required** — live photo estimation calls the Anthropic "
            "vision API. Set `ANTHROPIC_API_KEY` in the shell that launches the "
            "app, then restart. Simulation mode works fully offline."
        )
        render_live_plan(bins_df, n_trucks, truck_capacity_kg, shift_hours)
        return

    known_site_ids = bins_df["bin_id"].astype(str).tolist()
    uploads = st.file_uploader(
        "Container photos (multiple allowed)",
        type=UPLOAD_TYPES,
        accept_multiple_files=True,
        help="Whole container visible, 2–5 m distance (capture protocol, SPEC_V1 6.2).",
    )

    estimates_cache = st.session_state.setdefault(SESSION_PHOTO_ESTIMATES, {})
    current: list[dict] = []
    errors: list[dict] = []
    for upload in uploads or []:
        key = f"{upload.name}:{upload.size}"
        if key not in estimates_cache:
            with st.spinner(f"Estimating fill level: {upload.name}"):
                estimates_cache[key] = estimate_uploaded_photo(upload)
        result = estimates_cache[key]
        (errors if "error" in result else current).append(result)

    for result in errors:
        st.error(f"{result['photo']}: {result['error']}")
    if errors and st.button("Retry failed photos"):
        failed_names = {result["photo"] for result in errors}
        for key in [k for k in estimates_cache if k.rsplit(":", 1)[0] in failed_names]:
            del estimates_cache[key]
        st.rerun()

    if not current:
        st.info(
            "Upload container photos to estimate fill levels. Each photo gets a class "
            "(empty / half / full / overflowing) and a confidence from the live model."
        )
    else:
        edited = render_photo_estimates_table(current, known_site_ids)
        confirmed = confirmed_observations(edited)
        excluded = len(edited) - len(confirmed)
        if excluded:
            st.caption(
                f"{excluded} row(s) stay out of the plan: unchecked, no site assigned, "
                "or still uncertain."
            )
        if st.button(
            f"Add to today's plan ({len(confirmed)} site(s))",
            type="primary",
            disabled=confirmed.empty,
        ):
            st.session_state[SESSION_LIVE_PLAN] = merge_observations(
                st.session_state.get(SESSION_LIVE_PLAN), confirmed
            )

    render_live_plan(bins_df, n_trucks, truck_capacity_kg, shift_hours)


def render_live_photo_mode(
    bins_df: pd.DataFrame, n_trucks: int, truck_capacity_kg: float, shift_hours: float
) -> None:
    st.title("EcoRoute AI — Live photo mode")
    st.caption(
        "Fill levels on this page come from the live photo estimator (VLM v0), not the "
        "simulation. Site coordinates still come from the demo registry."
    )
    plan_tab, eval_tab = st.tabs(["Plan from photos", "Photo model evaluation"])
    with plan_tab:
        render_photo_plan_tab(bins_df, n_trucks, truck_capacity_kg, shift_hours)
    with eval_tab:
        render_photo_eval_page()


inject_styles()

with st.sidebar:
    st.header("Operations Control")
    mode = st.radio(
        "Mode",
        list(MODES),
        index=0,
        key=MODE_TOGGLE_KEY,
        help="Simulation: synthetic 180-bin sandbox for the demo. "
        "Live photo: upload real container photos; confirmed sites form today's plan.",
    )
    threshold, district_filter, waste_type_filter = 75, "All", "All"
    if mode == MODE_SIMULATION:
        threshold = st.slider("Collection threshold (%)", min_value=50, max_value=95, value=75, step=5)
        district_filter = st.selectbox("District", ["All"] + DISTRICTS)
        waste_type_filter = st.selectbox("Waste type", ["All"] + WASTE_TYPES)
    st.subheader("Routing")
    if mode == MODE_SIMULATION:
        engine = st.radio(
            "Engine",
            [ENGINE_CVRP, ENGINE_CLASSIC],
            index=0,
            help="CVRP plans per-truck routes with capacity and shift limits; "
            "Classic keeps the legacy single-loop 2-opt route.",
        )
    else:
        engine = ENGINE_CVRP
        st.caption("Live photo mode always plans with CVRP (OR-Tools).")
    n_trucks = st.slider("Trucks", min_value=MIN_TRUCKS, max_value=MAX_TRUCKS, value=2)
    truck_capacity_kg = st.number_input(
        "Truck capacity (kg)",
        min_value=500,
        max_value=20_000,
        value=int(DEFAULT_TRUCK_CAPACITY_KG),
        step=500,
    )
    shift_hours = st.slider("Shift length (h)", min_value=4, max_value=12, value=DEFAULT_SHIFT_HOURS)
    if mode == MODE_SIMULATION:
        st.markdown(
            """
            <div class="sidebar-note">
                <strong>Demo scale</strong>
                180 live bins represent one dispatch zone for a truck shift. The model trains on
                4,500 synthetic historical observations so the demo stays fast and stable.
            </div>
            """,
            unsafe_allow_html=True,
        )


if mode == MODE_LIVE_PHOTO:
    with st.spinner("Preparing site registry..."):
        ensure_data_exists()
    render_live_photo_mode(load_bins(), int(n_trucks), float(truck_capacity_kg), float(shift_hours))
    st.stop()


render_hero(threshold, district_filter, waste_type_filter)
render_pipeline(threshold, engine)
render_simulated_banner()

with st.spinner("Preparing data and model..."):
    ensure_data_exists()
    if not MODEL_PATH.exists():
        train_and_save_model()

bins_df = load_bins()
all_predicted_df = predict_fill_levels(bins_df, threshold=threshold)
predicted_df = all_predicted_df.copy()
if district_filter != "All":
    predicted_df = predicted_df[predicted_df["district"] == district_filter]
if waste_type_filter != "All":
    predicted_df = predicted_df[predicted_df["waste_type"] == waste_type_filter]

selected_bins_df = predicted_df[predicted_df["must_serve"]].copy()
route_comparison = apply_road_distances(compare_routes(predicted_df, selected_bins_df, DEPOT))

with st.spinner("Solving CVRP plan..."):
    cvrp_plan = solve_cvrp(selected_bins_df, int(n_trucks), float(truck_capacity_kg), float(shift_hours))
cvrp_active = engine == ENGINE_CVRP and not cvrp_plan.violations and not selected_bins_df.empty

if cvrp_active:
    savings_comparison = {
        **route_comparison,
        "selected_optimized_distance_km": round(cvrp_plan.total_distance_m / 1000, 3),
    }
    map_context_comparison = {
        **savings_comparison,
        "distance_source": cvrp_plan.distance_source,
    }
    map_fourth_stat = (f"{len(cvrp_plan.routes)}/{n_trucks}", "trucks routed (CVRP)")
    route_order_df = cvrp_route_order_df(cvrp_plan, selected_bins_df)
else:
    savings_comparison = route_comparison
    map_context_comparison = route_comparison
    map_fourth_stat = None
    route_order_df = route_points_to_dataframe(route_comparison["route_points_optimized"])

savings = calculate_savings(predicted_df, selected_bins_df, savings_comparison)

metrics = load_metrics()
if metrics:
    st.caption(
        f"Demo model: {metrics.get('model_name', 'RandomForestRegressor')} | "
        f"MAE {metrics.get('mae')} | RMSE {metrics.get('rmse')} | R² {metrics.get('r2')} — "
        "validated on simulated data only, not a field-accuracy claim."
    )

render_recommendation(selected_bins_df, predicted_df, savings)
render_critical_alert(selected_bins_df)
if engine == ENGINE_CVRP and cvrp_plan.violations:
    render_cvrp_violations(cvrp_plan)
render_kpis(savings, predicted_df)

render_map_context(
    predicted_df, selected_bins_df, savings, map_context_comparison, fourth_stat=map_fourth_stat
)
if cvrp_active:
    map_figure = create_fleet_route_map(
        predicted_df,
        selected_bins_df,
        plan_truck_route_points(cvrp_plan, selected_bins_df),
        DEPOT,
        threshold,
    )
else:
    map_figure = create_route_map(
        predicted_df,
        selected_bins_df,
        route_comparison["route_points_optimized"],
        DEPOT,
        threshold,
    )
stretch_plotly_chart(map_figure)

render_engine_comparison(route_comparison, cvrp_plan, int(n_trucks), float(truck_capacity_kg))

render_simulated_banner()
chart_col_1, chart_col_2 = st.columns(2)

with chart_col_1:
    route_chart_df = pd.DataFrame(
        {
            "Route": ["Fixed route", "Selected greedy route", "Selected 2-opt route"],
            "Distance (km)": [
                route_comparison["fixed_route_distance_km"],
                route_comparison["selected_greedy_distance_km"],
                route_comparison["selected_optimized_distance_km"],
            ],
        }
    )
    fig_route = px.bar(
        route_chart_df,
        x="Distance (km)",
        y="Route",
        orientation="h",
        title="Route distance comparison",
        color="Route",
        text="Distance (km)",
        color_discrete_map={
            "Fixed route": CHART_COLORS["fixed"],
            "Selected greedy route": CHART_COLORS["greedy"],
            "Selected 2-opt route": CHART_COLORS["optimized"],
        },
    )
    fig_route.update_traces(
        marker_line_width=0,
        texttemplate="%{x:.1f} km",
        textposition="outside",
        cliponaxis=False,
        hovertemplate="%{y}<br>%{x:.1f} km<extra></extra>",
    )
    fig_route.update_layout(showlegend=False)
    fig_route.update_yaxes(
        categoryorder="array",
        categoryarray=["Selected 2-opt route", "Selected greedy route", "Fixed route"],
    )
    apply_chart_theme(fig_route, height=360)
    fig_route.update_yaxes(title_text=None)
    fig_route.update_layout(margin={"l": 160, "r": 42, "t": 58, "b": 46})
    stretch_plotly_chart(fig_route)

with chart_col_2:
    fig_distribution = px.histogram(
        predicted_df,
        x="predicted_fill_pct",
        nbins=18,
        title="Predicted fill level distribution",
        labels={"predicted_fill_pct": "Predicted fill (%)", "count": "Bins"},
        color_discrete_sequence=[CHART_COLORS["blue"]],
    )
    fig_distribution.add_vrect(
        x0=threshold,
        x1=100,
        fillcolor="#ecfdf3",
        opacity=0.55,
        layer="below",
        line_width=0,
    )
    fig_distribution.add_vline(
        x=threshold,
        line_dash="dash",
        line_width=2,
        line_color=PRIORITY_COLORS["Critical"],
        annotation_text=f"{threshold}% threshold",
        annotation_position="top right",
    )
    fig_distribution.update_traces(
        marker_line_color="#ffffff",
        marker_line_width=1,
        opacity=0.9,
        hovertemplate="Predicted fill %{x}<br>Bins %{y}<extra></extra>",
    )
    apply_chart_theme(fig_distribution, height=360)
    fig_distribution.update_yaxes(title_text="Bins")
    stretch_plotly_chart(fig_distribution)

chart_col_3, chart_col_4 = st.columns(2)

with chart_col_3:
    if selected_bins_df.empty:
        st.info("No selected bins for the district workload chart at the current threshold.")
    else:
        district_workload = (
            selected_bins_df.groupby("district", as_index=False)
            .size()
            .rename(columns={"size": "selected_bins"})
            .sort_values("selected_bins", ascending=True)
        )
        fig_district = px.bar(
            district_workload,
            x="selected_bins",
            y="district",
            orientation="h",
            title="Selected bins by district",
            labels={"district": "District", "selected_bins": "Selected bins"},
            text="selected_bins",
            color_discrete_sequence=[CHART_COLORS["greedy"]],
        )
        fig_district.update_traces(
            marker_line_width=0,
            textposition="outside",
            cliponaxis=False,
            hovertemplate="%{y}<br>%{x} selected bins<extra></extra>",
        )
        apply_chart_theme(fig_district, height=360)
        fig_district.update_yaxes(title_text=None)
        fig_district.update_layout(margin={"l": 120, "r": 36, "t": 58, "b": 46})
        stretch_plotly_chart(fig_district)

with chart_col_4:
    priority_summary = (
        predicted_df["priority"]
        .value_counts()
        .reindex(["Critical", "High", "Medium", "Skip"], fill_value=0)
        .reset_index()
    )
    priority_summary.columns = ["priority", "count"]
    priority_summary = priority_summary.sort_values("count", ascending=True)
    fig_priority = px.bar(
        priority_summary,
        x="count",
        y="priority",
        orientation="h",
        title="Priority summary",
        labels={"priority": "Priority", "count": "Bins"},
        text="count",
        color="priority",
        color_discrete_map=PRIORITY_COLORS,
    )
    fig_priority.update_traces(
        marker_line_width=0,
        textposition="outside",
        textfont={"color": CHART_COLORS["text"]},
        cliponaxis=False,
        hovertemplate="%{y}<br>%{x} bins<extra></extra>",
    )
    apply_chart_theme(fig_priority, height=360)
    fig_priority.update_yaxes(title_text=None)
    fig_priority.update_layout(showlegend=False, margin={"l": 96, "r": 36, "t": 58, "b": 46})
    stretch_plotly_chart(fig_priority)

render_scenario_cards(predicted_df)

with st.expander("Why 180 bins and 4,500 training rows?"):
    st.markdown(
        """
        - `180` current bins is a one-shift dispatch zone, not every bin in the whole city. It is dense enough
          to prove routing savings, but still readable on one map.
        - `4,500` training rows are historical-style observations. More synthetic rows can make training slower
          without adding much signal once the model has learned the feature patterns.
        - Hundreds or thousands of live bins become a fleet-planning problem: split by district, truck capacity,
          shift window, and depot, then optimize each truck route separately. One giant route would be slower,
          harder to read, and less realistic for operations.
        """
    )

with st.expander("Model internals: feature importance"):
    feature_fig = model_feature_importance_chart()
    if feature_fig is not None:
        stretch_plotly_chart(feature_fig)

st.subheader("Selected bins for collection")
render_simulated_banner()
selected_table_columns = [
    "bin_id",
    "district",
    "waste_type",
    "predicted_fill_pct",
    "priority",
    "latitude",
    "longitude",
]
download_col_1, download_col_2 = st.columns(2)
with download_col_1:
    stretch_download_button(
        "Download selected bins CSV",
        data=selected_bins_df[selected_table_columns].to_csv(index=False).encode("utf-8"),
        file_name="ecoroute_selected_bins.csv",
        mime="text/csv",
    )
with download_col_2:
    stretch_download_button(
        "Download route order CSV",
        data=route_order_df.to_csv(index=False).encode("utf-8"),
        file_name="ecoroute_route_order.csv",
        mime="text/csv",
    )

stretch_dataframe(
    selected_bins_df[selected_table_columns].sort_values("predicted_fill_pct", ascending=False),
    hide_index=True,
)

with st.expander("Truck route order"):
    stretch_dataframe(
        route_order_df,
        hide_index=True,
    )

with st.expander("All predicted bins"):
    stretch_dataframe(
        predicted_df[selected_table_columns].sort_values("predicted_fill_pct", ascending=False),
        hide_index=True,
    )

if engine == ENGINE_CVRP:
    st.info(
        "How EcoRoute AI works: 1. Predict fill level using ML. 2. Select bins above the collection "
        "threshold. 3. Solve a capacitated vehicle routing problem (OR-Tools) with truck capacity and "
        "shift limits. 4. Estimate distance, time, fuel, cost, and CO₂ savings."
    )
else:
    st.info(
        "How EcoRoute AI works: 1. Predict fill level using ML. 2. Select bins above the collection threshold. "
        "3. Build a route using nearest-neighbor. 4. Improve it with 2-opt. 5. Estimate distance, time, fuel, "
        "cost, and CO₂ savings."
    )
