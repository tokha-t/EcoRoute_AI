"""Thirty-day fixed-versus-predictive simulation and honest report writer."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, Sequence

import numpy as np
import pandas as pd

from src.config import (
    CO2_KG_PER_LITER_DIESEL,
    DENSITY_KG_PER_L,
    DEPOT_COORDS,
    DETOUR_BUDGET_M_PER_M3,
    FALLBACK_COST_PER_M3_M,
    FUEL_CONSUMPTION_LITERS_PER_KM,
    FUEL_COST_KZT_PER_LITER,
    LANDFILL_COORDS,
    MAX_DUMP_TRIPS,
    MAX_INTERVAL_DAYS,
    SIMULATION_DAYS,
)
from src.optimize.distances import DistanceMatrix, get_matrix
from src.optimize.solver import Plan, Route, SolverParams, Truck, plan_routes
from src.sim.fill import ClassificationParams, advance_day, classify, empty_sites
from src.sim.provenance import area_type_mix_text, sector_scope_warning, site_provenance_text
from src.sim.world import generate_world

Policy = Literal["fixed", "fixed_naive", "predictive", "predictive_reds_only"]
POLICIES: tuple[Policy, ...] = (
    "fixed",
    "fixed_naive",
    "predictive",
    "predictive_reds_only",
)
FIXED_INTERVAL_DAYS = {"multistorey": 1, "private": 3, "commercial": 1, "mixed": 2}
REPORT_DIR = Path(__file__).resolve().parents[2] / "reports"
DEFAULT_REPORT_MD = REPORT_DIR / "simulation_30d.md"
DEFAULT_REPORT_CSV = REPORT_DIR / "simulation_30d.csv"
DEFAULT_SWEEP_CSV = REPORT_DIR / "yellow_detour_budget_sweep.csv"
DEFAULT_SWEEP_SVG = REPORT_DIR / "yellow_detour_frontier.svg"
DETOUR_BUDGET_SWEEP = (0.0, 100.0, 200.0, 400.0, 800.0, 1600.0)
REPORT_SOLVER_TIME_LIMIT_S = 5.0
REPORT_SOLVER_SOLUTION_LIMIT = 100


@dataclass(frozen=True)
class DailyRecord:
    policy: str
    day: int
    km_total: float
    km_per_tonne: float
    overflow_events: int
    overflow_site_days: float
    mean_fill_at_pickup: float
    max_interval_violations: int
    truck_hours: float
    fuel_liters: float
    co2_kg: float
    cost_kzt: float
    sites_served: int
    tonnes_collected: float
    distance_source: str
    valid_plan: bool


@dataclass(frozen=True)
class SweepSelection:
    detour_budget_m_per_m3: float
    dominates_fixed: bool
    reason: str


def default_trucks(count: int = 4, capacity_kg: float = 5_000.0) -> list[Truck]:
    return [Truck(f"TRUCK-{index + 1}", capacity_kg=capacity_kg) for index in range(count)]


def _world_points(world: pd.DataFrame) -> list[tuple[float, float]]:
    return (
        [DEPOT_COORDS] + list(zip(world["lat"].astype(float), world["lon"].astype(float))) + [LANDFILL_COORDS]
    )


def _subset_matrix(full: DistanceMatrix, world_indices: Sequence[int], world_size: int) -> DistanceMatrix:
    indices = [0] + [int(index) + 1 for index in world_indices] + [world_size + 1]
    return DistanceMatrix(
        seconds=[[full.seconds[i][j] for j in indices] for i in indices],
        meters=[[full.meters[i][j] for j in indices] for i in indices],
        fallback_used=full.fallback_used,
        source=full.source,
    )


def _solver_frame(world: pd.DataFrame, mask: pd.Series) -> tuple[pd.DataFrame, list[int]]:
    indices = [int(value) for value in world.index[mask]]
    return world.loc[indices].reset_index(drop=True), indices


def _fixed_candidates(world: pd.DataFrame, day: int) -> tuple[pd.DataFrame, list[int]]:
    due = pd.Series(
        [
            day - int(last_service_day) >= FIXED_INTERVAL_DAYS.get(str(area), 2)
            for area, last_service_day in zip(world["area_type"], world["last_service_day"])
        ],
        index=world.index,
    )
    sites, indices = _solver_frame(world, due)
    sites["klass"] = "RED"
    sites["reason"] = "fixed_schedule"
    sites["must_serve"] = True
    return sites, indices


def _predictive_candidates(
    world: pd.DataFrame,
    day: int,
    classification_params: ClassificationParams,
) -> tuple[pd.DataFrame, list[int]]:
    classified = classify(world, day, classification_params)
    mask = classified["klass"].isin(["RED", "YELLOW"])
    return _solver_frame(classified, mask)


def _naive_plan(
    sites: pd.DataFrame,
    trucks: Sequence[Truck],
    matrix: DistanceMatrix,
    params: SolverParams,
) -> Plan:
    """Route fixed-schedule sites in site-id order, preserving capacity resets."""
    if sites.empty:
        return Plan([], [], [], 0.0, 0.0, "none", False, mode="fixed_naive")
    ordered = sites.sort_values("site_id").reset_index(drop=True)
    # The caller already orders the matrix like sites. Reindex it if sorting changed.
    original_by_id = {str(value): index + 1 for index, value in enumerate(sites["site_id"])}
    order = [0] + [original_by_id[str(value)] for value in ordered["site_id"]] + [len(sites) + 1]
    local = DistanceMatrix(
        seconds=[[matrix.seconds[i][j] for j in order] for i in order],
        meters=[[matrix.meters[i][j] for j in order] for i in order],
        fallback_used=matrix.fallback_used,
        source=matrix.source,
    )
    fill = ordered["fill_pct"].astype(float).clip(0, 100)
    loads = (ordered["capacity_liters"].astype(float) * fill / 100 * DENSITY_KG_PER_L).tolist()
    landfill_node = len(ordered) + 1
    chunks = np.array_split(np.arange(1, len(ordered) + 1), len(trucks))
    routes: list[Route] = []
    unserved: list[str] = []
    for truck, raw_nodes in zip(trucks, chunks):
        nodes = [int(value) for value in raw_nodes]
        if not nodes:
            continue
        path = [0]
        current_load = 0.0
        max_segment = 0.0
        dumps = 0
        served_nodes: list[int] = []
        for node in nodes:
            load = loads[node - 1]
            if load > truck.capacity_kg:
                unserved.append(str(ordered.iloc[node - 1]["site_id"]))
                continue
            if current_load + load > truck.capacity_kg:
                # Keep one landfill visit available for the mandatory final unload.
                if dumps + 1 >= params.max_dump_trips:
                    unserved.append(str(ordered.iloc[node - 1]["site_id"]))
                    continue
                path.append(landfill_node)
                dumps += 1
                current_load = 0.0
            path.append(node)
            served_nodes.append(node)
            current_load += load
            max_segment = max(max_segment, current_load)
        if not served_nodes:
            continue
        path.append(landfill_node)
        dumps += 1
        current_load = 0.0
        path.append(0)
        distance = sum(local.meters[a][b] for a, b in zip(path[:-1], path[1:]))
        duration = sum(local.seconds[a][b] for a, b in zip(path[:-1], path[1:]))
        duration += len(served_nodes) * params.service_time_s + dumps * params.landfill_service_s
        cumulative_load = [0.0]
        running_load = 0.0
        for node in path[1:]:
            if node == landfill_node or node == 0:
                running_load = 0.0
            else:
                running_load += loads[node - 1]
            cumulative_load.append(running_load)
        routes.append(
            Route(
                truck_id=truck.truck_id,
                site_ids=[str(ordered.iloc[node - 1]["site_id"]) for node in served_nodes],
                load_kg=sum(loads[node - 1] for node in served_nodes),
                duration_s=duration,
                distance_m=distance,
                dump_stops=dumps,
                ordered_stops=[
                    "LANDFILL" if node == landfill_node else str(ordered.iloc[node - 1]["site_id"])
                    for node in path[1:-1]
                ],
                max_segment_load_kg=max_segment,
                cumulative_distance_m=list(
                    np.cumsum([0.0] + [local.meters[a][b] for a, b in zip(path[:-1], path[1:])])
                ),
                cumulative_duration_s=list(
                    np.cumsum(
                        [0.0]
                        + [
                            local.seconds[a][b]
                            + (
                                params.landfill_service_s
                                if a == landfill_node
                                else params.service_time_s
                                if a > 0
                                else 0.0
                            )
                            for a, b in zip(path[:-1], path[1:])
                        ]
                    )
                ),
                cumulative_load_kg=cumulative_load,
                end_load_kg=current_load,
            )
        )
    violations = [f"Не обслужены обязательные площадки: {', '.join(unserved)}"] if unserved else []
    return Plan(
        routes=routes,
        violations=violations,
        dropped_site_ids=[],
        total_distance_m=sum(route.distance_m for route in routes),
        total_duration_s=sum(route.duration_s for route in routes),
        distance_source=local.source,
        fallback_used=local.fallback_used,
        unserved_red=unserved,
        mode="fixed_naive",
    )


def _daily_record(
    policy: Policy,
    day: int,
    before_service: pd.DataFrame,
    after_service: pd.DataFrame,
    plan: Plan,
) -> DailyRecord:
    served_ids = [site_id for route in plan.routes for site_id in route.site_ids]
    served = before_service[before_service["site_id"].astype(str).isin(set(served_ids))]
    liters = served["capacity_liters"].astype(float) * served["fill_pct"].astype(float).clip(0, 100) / 100
    tonnes = float(liters.sum() * DENSITY_KG_PER_L / 1000)
    km = plan.total_distance_m / 1000
    overflow = int((before_service["fill_pct"] > 100).sum())
    fuel = km * FUEL_CONSUMPTION_LITERS_PER_KM
    interval_violations = int(
        ((day - after_service["last_service_day"].astype(int)) > MAX_INTERVAL_DAYS).sum()
    )
    return DailyRecord(
        policy=policy,
        day=day,
        km_total=km,
        km_per_tonne=km / tonnes if tonnes else 0.0,
        overflow_events=overflow,
        overflow_site_days=overflow / max(len(before_service), 1) * 1000,
        mean_fill_at_pickup=float(served["fill_pct"].mean()) if not served.empty else 0.0,
        max_interval_violations=interval_violations,
        truck_hours=plan.total_duration_s / 3600,
        fuel_liters=fuel,
        co2_kg=fuel * CO2_KG_PER_LITER_DIESEL,
        cost_kzt=fuel * FUEL_COST_KZT_PER_LITER,
        sites_served=len(served_ids),
        tonnes_collected=tonnes,
        distance_source=plan.distance_source,
        valid_plan=not plan.violations and not plan.unserved_red,
    )


def simulate(
    world: pd.DataFrame,
    policy: Policy,
    days: int = SIMULATION_DAYS,
    seed: int = 42,
    *,
    trucks: Sequence[Truck] | None = None,
    classification_params: ClassificationParams | None = None,
    detour_budget_m_per_m3: float = DETOUR_BUDGET_M_PER_M3,
    matrix: DistanceMatrix | None = None,
) -> list[DailyRecord]:
    """Run one policy; stochastic accumulation is repeatable under ``seed``."""
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}; expected one of {POLICIES}")
    state = world.copy().reset_index(drop=True)
    trucks = list(trucks or default_trucks())
    classification_params = classification_params or ClassificationParams()
    full_matrix = matrix or get_matrix(_world_points(state), timeout=0.5)
    rng = np.random.default_rng(seed)
    records: list[DailyRecord] = []
    for day in range(1, days + 1):
        state = advance_day(state, day, rng)
        before_service = state.copy()
        if policy in {"fixed", "fixed_naive"}:
            candidates, indices = _fixed_candidates(state, day)
        else:
            candidates, indices = _predictive_candidates(state, day, classification_params)
        local_matrix = _subset_matrix(full_matrix, indices, len(state))
        params = SolverParams(
            depot=DEPOT_COORDS,
            landfill=LANDFILL_COORDS,
            matrix=local_matrix,
            max_dump_trips=MAX_DUMP_TRIPS,
            fallback_cost_per_m3_m=FALLBACK_COST_PER_M3_M,
            detour_budget_m_per_m3=detour_budget_m_per_m3,
            reds_only=policy == "predictive_reds_only",
            # Fixed solution-count termination makes the report independent
            # of CPU speed; the wall-clock limit is only a safety valve.
            time_limit_s=REPORT_SOLVER_TIME_LIMIT_S,
            solution_limit=REPORT_SOLVER_SOLUTION_LIMIT,
        )
        if candidates.empty:
            plan = Plan([], [], [], 0.0, 0.0, "none", False, mode=policy)
        elif policy == "fixed_naive":
            plan = _naive_plan(candidates, trucks, local_matrix, params)
        else:
            plan = plan_routes(candidates, trucks, params)
        served_ids = [site_id for route in plan.routes for site_id in route.site_ids]
        state = empty_sites(state, served_ids, day)
        records.append(_daily_record(policy, day, before_service, state, plan))
    if policy == "fixed":
        violations = sum(record.max_interval_violations for record in records)
        if violations:
            raise RuntimeError(
                "Fixed baseline is not max-interval compliant: "
                f"{violations} violations with FIXED_INTERVAL_DAYS={FIXED_INTERVAL_DAYS} "
                f"and MAX_INTERVAL_DAYS={MAX_INTERVAL_DAYS}."
            )
    return records


def summarize(records: Sequence[DailyRecord], site_count: int) -> dict[str, float]:
    days = max(len(records), 1)
    total_tonnes = sum(record.tonnes_collected for record in records)
    total_km = sum(record.km_total for record in records)
    total_served = sum(record.sites_served for record in records)
    overflow = sum(record.overflow_events for record in records)
    return {
        "km_total": total_km,
        "km_per_tonne": total_km / total_tonnes if total_tonnes else 0.0,
        "overflow_events": float(overflow),
        "overflow_site_days": overflow / max(site_count * days, 1) * 1000,
        "mean_fill_at_pickup": (
            sum(record.mean_fill_at_pickup * record.sites_served for record in records) / total_served
            if total_served
            else 0.0
        ),
        "max_interval_violations": float(sum(record.max_interval_violations for record in records)),
        "truck_hours": sum(record.truck_hours for record in records),
        "fuel_liters": sum(record.fuel_liters for record in records),
        "co2_kg": sum(record.co2_kg for record in records),
        "cost_kzt": sum(record.cost_kzt for record in records),
        "sites_served": float(total_served),
    }


def select_detour_budget(
    sweep_summaries: dict[float, dict[str, float]],
    fixed_summary: dict[str, float],
) -> SweepSelection:
    """Select the largest detour budget strictly improving fixed km and overflow."""
    dominating = [
        budget
        for budget, summary in sweep_summaries.items()
        if summary["km_total"] < fixed_summary["km_total"]
        and summary["overflow_events"] < fixed_summary["overflow_events"]
    ]
    if dominating:
        chosen = max(dominating)
        summary = sweep_summaries[chosen]
        return SweepSelection(
            detour_budget_m_per_m3=chosen,
            dominates_fixed=True,
            reason=(
                f"{chosen:g} m/m³ is the largest tested detour budget below fixed on both measures: "
                f"{summary['km_total']:.1f} km and {summary['overflow_events']:.0f} overflow "
                f"events versus fixed at {fixed_summary['km_total']:.1f} km and "
                f"{fixed_summary['overflow_events']:.0f}."
            ),
        )
    chosen = min(
        sweep_summaries,
        key=lambda budget: (sweep_summaries[budget]["km_total"], budget),
    )
    summary = sweep_summaries[chosen]
    return SweepSelection(
        detour_budget_m_per_m3=chosen,
        dominates_fixed=False,
        reason=(
            "No tested detour budget beats fixed on both total distance and overflow events; "
            f"{chosen:g} m/m³ is the distance-minimising fallback at {summary['km_total']:.1f} km "
            f"and {summary['overflow_events']:.0f} overflow events (fixed: "
            f"{fixed_summary['km_total']:.1f} km, {fixed_summary['overflow_events']:.0f})."
        ),
    )


def run_detour_budget_sweep(
    world: pd.DataFrame,
    days: int = SIMULATION_DAYS,
    seed: int = 42,
    *,
    trucks: Sequence[Truck] | None = None,
    classification_params: ClassificationParams | None = None,
    budgets: Sequence[float] = DETOUR_BUDGET_SWEEP,
    matrix: DistanceMatrix | None = None,
) -> dict[float, list[DailyRecord]]:
    """Run all marginal-detour candidates against identical daily fills."""
    full_matrix = matrix or get_matrix(_world_points(world), timeout=0.5)
    return {
        float(budget): simulate(
            world,
            "predictive",
            days,
            seed,
            trucks=trucks,
            classification_params=classification_params,
            detour_budget_m_per_m3=float(budget),
            matrix=full_matrix,
        )
        for budget in budgets
    }


def run_full_analysis(
    world: pd.DataFrame,
    days: int = SIMULATION_DAYS,
    seed: int = 42,
    *,
    trucks: Sequence[Truck] | None = None,
    classification_params: ClassificationParams | None = None,
) -> tuple[
    dict[str, list[DailyRecord]],
    dict[float, dict[str, float]],
    SweepSelection,
]:
    """Run baselines, sweep marginal detour budgets, and choose the report default."""
    full_matrix = get_matrix(_world_points(world), timeout=0.5)
    baselines = {
        policy: simulate(
            world,
            policy,
            days,
            seed,
            trucks=trucks,
            classification_params=classification_params,
            matrix=full_matrix,
        )
        for policy in ("fixed", "fixed_naive")
    }
    sweep_records = run_detour_budget_sweep(
        world,
        days,
        seed,
        trucks=trucks,
        classification_params=classification_params,
        matrix=full_matrix,
    )
    sweep_summaries = {budget: summarize(records, len(world)) for budget, records in sweep_records.items()}
    selection = select_detour_budget(sweep_summaries, summarize(baselines["fixed"], len(world)))
    return (
        {
            **baselines,
            "predictive": sweep_records[selection.detour_budget_m_per_m3],
            "predictive_reds_only": [
                replace(record, policy="predictive_reds_only") for record in sweep_records[0.0]
            ],
        },
        sweep_summaries,
        selection,
    )


def _write_sweep_svg(
    sweep_summaries: dict[float, dict[str, float]],
    path: Path,
    *,
    selected_budget: float | None = None,
    fixed_summary: dict[str, float] | None = None,
) -> Path:
    """Write a dependency-free distance/overflow frontier chart."""
    width, height = 760, 430
    left, right, top, bottom = 80, 30, 40, 65
    summaries_for_scale = list(sweep_summaries.values())
    if fixed_summary is not None:
        summaries_for_scale.append(fixed_summary)
    kms = [summary["km_total"] for summary in summaries_for_scale]
    overflows = [summary["overflow_events"] for summary in summaries_for_scale]
    min_km, max_km = min(kms), max(kms)
    min_overflow, max_overflow = min(overflows), max(overflows)

    def scale(value: float, low: float, high: float, start: float, end: float) -> float:
        return (start + end) / 2 if high == low else start + (value - low) / (high - low) * (end - start)

    points = []
    for budget, summary in sorted(sweep_summaries.items()):
        x = scale(summary["km_total"], min_km, max_km, left, width - right)
        y = scale(summary["overflow_events"], min_overflow, max_overflow, height - bottom, top)
        selected = selected_budget is not None and budget == selected_budget
        points.append(
            f"<circle cx='{x:.1f}' cy='{y:.1f}' r='{'9' if selected else '6'}' "
            f"fill='{'#16a34a' if selected else '#0ea5e9'}'>"
            f"<title>budget={budget:g} m/m³: {summary['km_total']:.1f} km, "
            f"{summary['overflow_events']:.0f} overflows</title></circle>"
            f"<text x='{x + 9:.1f}' y='{y - 7:.1f}' font-size='12'>"
            f"{budget:g} ({summary['km_total']:.0f}, "
            f"{summary['overflow_events']:.0f})</text>"
        )
    if fixed_summary is not None:
        x = scale(fixed_summary["km_total"], min_km, max_km, left, width - right)
        y = scale(fixed_summary["overflow_events"], min_overflow, max_overflow, height - bottom, top)
        points.append(
            f"<rect x='{x - 6:.1f}' y='{y - 6:.1f}' width='12' height='12' fill='#dc2626'>"
            f"<title>fixed: {fixed_summary['km_total']:.1f} km, "
            f"{fixed_summary['overflow_events']:.0f} overflows</title></rect>"
            f"<text x='{x + 9:.1f}' y='{y - 7:.1f}' font-size='12'>fixed "
            f"({fixed_summary['km_total']:.0f}, {fixed_summary['overflow_events']:.0f})</text>"
        )
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' "
        f"viewBox='0 0 {width} {height}'>"
        "<rect width='100%' height='100%' fill='white'/>"
        f"<line x1='{left}' y1='{height - bottom}' x2='{width - right}' y2='{height - bottom}' stroke='#334155'/>"
        f"<line x1='{left}' y1='{top}' x2='{left}' y2='{height - bottom}' stroke='#334155'/>"
        f"<text x='{width / 2:.0f}' y='{height - 18}' text-anchor='middle'>km_total</text>"
        f"<text x='20' y='{height / 2:.0f}' text-anchor='middle' transform='rotate(-90 20 {height / 2:.0f})'>overflow_events</text>"
        + "".join(points)
        + "</svg>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")
    return path


def write_comparison_report(
    world: pd.DataFrame,
    results: dict[str, Sequence[DailyRecord]],
    markdown_path: Path = DEFAULT_REPORT_MD,
    csv_path: Path = DEFAULT_REPORT_CSV,
    *,
    sweep_summaries: dict[float, dict[str, float]] | None = None,
    selection: SweepSelection | None = None,
    sweep_csv_path: Path = DEFAULT_SWEEP_CSV,
    sweep_svg_path: Path = DEFAULT_SWEEP_SVG,
) -> tuple[Path, Path]:
    """Write daily CSV and simulated comparison; safety KPIs flank distance."""
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [asdict(record) for policy_records in results.values() for record in policy_records]
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    summaries = {name: summarize(records, len(world)) for name, records in results.items()}
    baseline = summaries["fixed"]
    metrics = list(next(iter(summaries.values())))
    distance_sources = sorted(
        {
            record.distance_source
            for policy_records in results.values()
            for record in policy_records
            if record.distance_source != "none"
        }
    )
    sector = str(world["sector"].iloc[0]) if "sector" in world and not world.empty else "all"
    scope_warning = sector_scope_warning(world, "en")
    lines = [
        "# 30-day predictive collection simulation",
        "",
        "> **SIMULATED DATA.** This proves policy logic on a modeled Baikonur district; "
        "real savings must be measured during the pilot.",
        "",
        f"World: {len(world)} sites in sector {sector}.",
        "",
        *([f"> **SECTOR SCOPE WARNING.** {scope_warning}", ""] if scope_warning else []),
        f"**{site_provenance_text(world, 'ru')}**",
        "",
        f"Area-type composition: {area_type_mix_text(world)}.",
        "",
        "The fixed baseline is a compliant, idealised calendar. A real operator also reacts "
        "to complaints, so actual current practice lies somewhere between fixed and predictive.",
        "",
        "Distance source: "
        + ", ".join(distance_sources or ["none"])
        + (" (road-distance fallback, explicitly flagged)." if "haversine" in distance_sources else "."),
        "",
        "Distance savings are never presented alone: overflow events and max-interval violations "
        "are shown in the same table.",
        "",
        "| Policy | km_total | Δ vs fixed | overflow_events | max_interval_violations |",
        "|---|---:|---:|---:|---:|",
    ]
    for policy, summary in summaries.items():
        delta = (
            (summary["km_total"] - baseline["km_total"]) / baseline["km_total"] * 100
            if baseline["km_total"]
            else 0.0
        )
        lines.append(
            f"| {policy} | {summary['km_total']:.2f} | {delta:+.1f}% | "
            f"{summary['overflow_events']:.0f} | {summary['max_interval_violations']:.0f} |"
        )
    lines.extend(
        ["", "## All KPIs", "", "| KPI | " + " | ".join(summaries) + " |", "|---|" + "---:|" * len(summaries)]
    )
    for metric in metrics:
        values = " | ".join(f"{summaries[policy][metric]:.2f}" for policy in summaries)
        lines.append(f"| {metric} | {values} |")
    lines.extend(["", "## Predictive delta vs fixed", "", "| KPI | Delta |", "|---|---:|"])
    for metric in metrics:
        fixed_value = baseline[metric]
        value = summaries["predictive"][metric]
        delta = (value - fixed_value) / fixed_value * 100 if fixed_value else 0.0
        lines.append(f"| {metric} | {delta:+.1f}% |")
    predictive = summaries["predictive"]
    reds_only = summaries["predictive_reds_only"]
    yellow_rule_active = any(
        predictive[key] != reds_only[key] for key in ("km_total", "overflow_events", "sites_served")
    )
    lines.extend(
        [
            "",
            (
                "The selected predictive policy differs from predictive_reds_only: "
                f"{predictive['sites_served'] - reds_only['sites_served']:+.0f} sites served, "
                f"{predictive['km_total'] - reds_only['km_total']:+.1f} km, and "
                f"{predictive['overflow_events'] - reds_only['overflow_events']:+.0f} overflow events."
                if yellow_rule_active
                else "**Yellow rule is inert at the selected default:** predictive and "
                "predictive_reds_only are identical on distance, overflow, and sites served."
            ),
        ]
    )
    if sweep_summaries is not None:
        sweep_rows = [
            {"detour_budget_m_per_m3": budget, **summary}
            for budget, summary in sorted(sweep_summaries.items())
        ]
        pd.DataFrame(sweep_rows).to_csv(sweep_csv_path, index=False)
        _write_sweep_svg(
            sweep_summaries,
            sweep_svg_path,
            selected_budget=(selection.detour_budget_m_per_m3 if selection else None),
            fixed_summary=baseline,
        )
        lines.extend(
            [
                "",
                "## Marginal YELLOW detour-budget trade-off sweep",
                "",
                "![Distance versus overflow frontier](yellow_detour_frontier.svg)",
                "",
                "| detour_budget_m_per_m3 | " + " | ".join(metrics) + " |",
                "|---:|" + "---:|" * len(metrics),
            ]
        )
        for budget, summary in sorted(sweep_summaries.items()):
            values = " | ".join(f"{summary[metric]:.2f}" for metric in metrics)
            lines.append(f"| {budget:g} | {values} |")
        if selection is not None:
            lines.extend(
                [
                    "",
                    "**Selected default: `DETOUR_BUDGET_M_PER_M3 = "
                    f"{selection.detour_budget_m_per_m3:g}`.** " + selection.reason,
                ]
            )
    lines.append("")
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return markdown_path, csv_path


def run_comparison(
    world: pd.DataFrame,
    days: int = SIMULATION_DAYS,
    seed: int = 42,
    *,
    trucks: Sequence[Truck] | None = None,
    classification_params: ClassificationParams | None = None,
    detour_budget_m_per_m3: float = DETOUR_BUDGET_M_PER_M3,
) -> dict[str, list[DailyRecord]]:
    full_matrix = get_matrix(_world_points(world), timeout=0.5)
    return {
        policy: simulate(
            world,
            policy,
            days,
            seed,
            trucks=trucks,
            classification_params=classification_params,
            detour_budget_m_per_m3=detour_budget_m_per_m3,
            matrix=full_matrix,
        )
        for policy in POLICIES
    }


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=SIMULATION_DAYS)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--world", type=Path, default=Path("data/world.csv"))
    args = parser.parse_args()
    world = pd.read_csv(args.world) if args.world.exists() else generate_world(seed=args.seed)
    results, sweep_summaries, selection = run_full_analysis(world, args.days, args.seed)
    md_path, csv_path = write_comparison_report(
        world,
        results,
        sweep_summaries=sweep_summaries,
        selection=selection,
    )
    print(f"Wrote simulated comparison to {md_path} and {csv_path}")


if __name__ == "__main__":
    _main()
