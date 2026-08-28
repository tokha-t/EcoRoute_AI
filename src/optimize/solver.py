"""Daily fleet routing for the legacy demo and SPEC V2 simulation.

plan_routes() assigns selected sites to a small fleet (1-3 trucks). Every
route starts and ends at the depot, respects the truck's capacity_kg and the
shift duration, and always includes must_serve sites; other sites may be
dropped (with a penalty) when constraints force it. Distances and travel
times come from src.optimize.distances.get_matrix (OSRM, haversine fallback),
and the plan carries the matrix source so the UI can label fallback honestly.

Site load estimate:
    load_kg = containers * capacity_liters * (fill_pct / 100) * DENSITY_KG_PER_L
Missing values are treated conservatively (full fill, largest standard
container, one container) so capacity is never underestimated.

The legacy dataframe contract still uses the original OR-Tools CVRP. V2 site
frames (identified by ``klass``/``site_id``) use a two-pass OR-Tools model.
Each pass receives duplicate optional landfill nodes whose negative demand and
load-dimension slack reset a truck's load; a second dimension caps dump visits.
RED nodes remain mandatory, while YELLOW disjunction penalties are scaled by
pickup volume and the RED-only reference cost. Dump service counts toward both
the objective and shift duration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil, floor
from typing import Sequence

import numpy as np
import pandas as pd
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from src.config import (
    DENSITY_KG_PER_L,
    FALLBACK_COST_PER_M3_M,
    LANDFILL_SERVICE_SECONDS,
    MAX_DUMP_TRIPS,
    YELLOW_TOLERANCE,
)
from src.optimize.distances import DistanceMatrix, Point, get_matrix

MIN_TRUCKS = 1
MAX_TRUCKS = 3
DEFAULT_TRUCK_CAPACITY_KG = 5_000.0  # spec acceptance sample: "2 trucks of 5 t"
DEFAULT_SHIFT_DURATION_S = 8 * 3600.0
DEFAULT_SERVICE_TIME_S = 180.0  # loading time per site stop
DEFAULT_SOLVER_TIME_LIMIT_S = 5.0
DEFAULT_CONTAINERS_PER_SITE = 1
FALLBACK_CAPACITY_LITERS = 1_100  # largest standard container, used for NaN

_REQUIRED_COLUMNS = ("bin_id", "latitude", "longitude", "capacity_liters")
_FILL_COLUMNS = ("fill_pct", "predicted_fill_pct")


@dataclass(frozen=True)
class Truck:
    truck_id: str
    capacity_kg: float = DEFAULT_TRUCK_CAPACITY_KG
    shift_seconds: float | None = None
    service_seconds_per_site: float | None = None


@dataclass(frozen=True)
class SolverParams:
    depot: Point  # (latitude, longitude)
    shift_duration_s: float = DEFAULT_SHIFT_DURATION_S
    service_time_s: float = DEFAULT_SERVICE_TIME_S
    time_limit_s: float = DEFAULT_SOLVER_TIME_LIMIT_S
    # Inject a precomputed matrix (rows ordered [depot] + sites_df rows) to
    # skip the OSRM/network path; None means call get_matrix().
    matrix: DistanceMatrix | None = None
    landfill: Point | None = None
    landfill_service_s: float = LANDFILL_SERVICE_SECONDS
    max_dump_trips: int = MAX_DUMP_TRIPS
    yellow_tolerance: float = YELLOW_TOLERANCE
    fallback_cost_per_m3_m: float = FALLBACK_COST_PER_M3_M
    reds_only: bool = False


@dataclass(frozen=True)
class Route:
    truck_id: str
    site_ids: list[str]  # visit order, depot start/end implicit
    load_kg: float
    duration_s: float
    distance_m: float
    dump_stops: int = 0
    ordered_stops: list[str] = field(default_factory=list)
    max_segment_load_kg: float = 0.0


@dataclass(frozen=True)
class YellowDecision:
    site_id: str
    insertion_cost_m: float
    penalty_m: float
    volume_m3: float
    served: bool
    explanation_ru: str


@dataclass(frozen=True)
class Plan:
    routes: list[Route]
    violations: list[str]  # must be empty for the plan to be renderable
    dropped_site_ids: list[str]
    total_distance_m: float
    total_duration_s: float
    distance_source: str  # "osrm" | "haversine" | "none"
    fallback_used: bool
    served_yellow: list[str] = field(default_factory=list)
    skipped_yellow: list[YellowDecision] = field(default_factory=list)
    unserved_red: list[str] = field(default_factory=list)
    reference_cost_m_per_m3: float = 0.0
    mode: str = "legacy"


def estimate_load_kg(sites_df: pd.DataFrame) -> pd.Series:
    """Estimated pickup mass per site, in kg.

    containers * capacity_liters * (fill / 100) * DENSITY_KG_PER_L, where fill
    comes from "fill_pct" (measured) or "predicted_fill_pct". NaN fill counts
    as 100%, NaN capacity as FALLBACK_CAPACITY_LITERS, NaN containers as 1, so
    an unknown site is planned at full weight rather than squeezed in.
    """
    fill_col = next((c for c in _FILL_COLUMNS if c in sites_df.columns), None)
    if fill_col is None:
        raise ValueError(f"sites_df needs one of {_FILL_COLUMNS} to estimate load")

    fill = pd.to_numeric(sites_df[fill_col], errors="coerce").fillna(100.0).clip(lower=0.0)
    capacity = (
        pd.to_numeric(sites_df["capacity_liters"], errors="coerce")
        .fillna(FALLBACK_CAPACITY_LITERS)
        .clip(lower=0.0)
    )
    if "containers" in sites_df.columns:
        containers = (
            pd.to_numeric(sites_df["containers"], errors="coerce")
            .fillna(DEFAULT_CONTAINERS_PER_SITE)
            .clip(lower=0.0)
        )
    else:
        containers = pd.Series(float(DEFAULT_CONTAINERS_PER_SITE), index=sites_df.index)

    return containers * capacity * (fill / 100.0) * DENSITY_KG_PER_L


def _validate(sites_df: pd.DataFrame, trucks: Sequence[Truck]) -> None:
    if not MIN_TRUCKS <= len(trucks) <= MAX_TRUCKS:
        raise ValueError(f"expected {MIN_TRUCKS}-{MAX_TRUCKS} trucks, got {len(trucks)}")
    if any(truck.capacity_kg <= 0 for truck in trucks):
        raise ValueError("every truck needs a positive capacity_kg")
    missing = [column for column in _REQUIRED_COLUMNS if column not in sites_df.columns]
    if missing:
        raise ValueError(f"sites_df is missing required columns: {missing}")


def _empty_plan(violations: list[str], matrix: DistanceMatrix | None) -> Plan:
    return Plan(
        routes=[],
        violations=violations,
        dropped_site_ids=[],
        total_distance_m=0.0,
        total_duration_s=0.0,
        distance_source=matrix.source if matrix else "none",
        fallback_used=matrix.fallback_used if matrix else False,
    )


def _preflight_violations(
    site_ids: list[str],
    loads: list[float],
    must_serve: list[bool],
    trucks: Sequence[Truck],
    seconds: list[list[float]],
    params: SolverParams,
) -> list[str]:
    """Necessary-condition checks with actionable messages before solving."""
    violations: list[str] = []
    max_capacity = max(truck.capacity_kg for truck in trucks)
    fleet_capacity = sum(truck.capacity_kg for truck in trucks)

    must_load = 0.0
    for position, (site_id, load) in enumerate(zip(site_ids, loads)):
        if not must_serve[position]:
            continue
        must_load += load
        if load > max_capacity:
            violations.append(
                f"Must-serve site {site_id} needs {load:.0f} kg, above the largest "
                f"truck capacity of {max_capacity:.0f} kg."
            )
        node = position + 1  # node 0 is the depot
        round_trip_s = seconds[0][node] + params.service_time_s + seconds[node][0]
        if round_trip_s > params.shift_duration_s:
            violations.append(
                f"Must-serve site {site_id} cannot be reached and returned within the "
                f"{params.shift_duration_s / 3600:.1f} h shift."
            )
    if must_load > fleet_capacity:
        violations.append(
            f"Must-serve load of {must_load:.0f} kg exceeds total fleet capacity "
            f"of {fleet_capacity:.0f} kg."
        )
    return violations


def plan_routes(sites_df: pd.DataFrame, trucks: Sequence[Truck], params: SolverParams) -> Plan:
    """Solve the CVRP for the selected sites. See module docstring for the contract."""
    trucks = list(trucks)
    if sites_df.empty:
        return _empty_plan(violations=[], matrix=None)
    if "klass" in sites_df.columns or "site_id" in sites_df.columns:
        return _plan_v2(sites_df, trucks, params)
    _validate(sites_df, trucks)

    sites = sites_df.reset_index(drop=True)
    site_ids = [str(value) for value in sites["bin_id"]]
    loads = [float(value) for value in estimate_load_kg(sites)]
    if "must_serve" in sites.columns:
        must_serve = [bool(value) for value in sites["must_serve"]]
    else:
        must_serve = [False] * len(sites)

    points: list[Point] = [params.depot] + [
        (float(row.latitude), float(row.longitude)) for row in sites.itertuples()
    ]
    matrix = params.matrix if params.matrix is not None else get_matrix(points)
    if len(matrix.seconds) != len(points):
        raise ValueError(
            f"injected matrix has {len(matrix.seconds)} rows, expected {len(points)} "
            "([depot] + sites_df rows)"
        )

    violations = _preflight_violations(site_ids, loads, must_serve, trucks, matrix.seconds, params)
    if violations:
        return _empty_plan(violations, matrix)

    solution, manager, routing = _solve(sites, loads, must_serve, trucks, matrix, params)
    if solution is None:
        return _empty_plan(
            ["Solver found no feasible plan for the must-serve sites with this fleet "
             "and shift length. Add a truck, raise capacity, or extend the shift."],
            matrix,
        )
    return _extract_plan(solution, manager, routing, site_ids, loads, trucks, matrix, params)


def _v2_frame(sites_df: pd.DataFrame) -> pd.DataFrame:
    sites = sites_df.copy().reset_index(drop=True)
    aliases = {
        "site_id": "_site_id",
        "bin_id": "_site_id",
        "lat": "_lat",
        "latitude": "_lat",
        "lon": "_lon",
        "longitude": "_lon",
    }
    for source, target in aliases.items():
        if target not in sites.columns and source in sites.columns:
            sites[target] = sites[source]
    missing = [name for name in ("_site_id", "_lat", "_lon", "capacity_liters", "fill_pct") if name not in sites]
    if missing:
        raise ValueError(f"V2 sites are missing required columns: {missing}")
    if "klass" not in sites:
        sites["klass"] = np.where(sites.get("must_serve", False), "RED", "YELLOW")
    sites["_site_id"] = sites["_site_id"].astype(str)
    return sites


def _v2_loads_and_volumes(sites: pd.DataFrame) -> tuple[list[float], list[float]]:
    fill = pd.to_numeric(sites["fill_pct"], errors="coerce").fillna(100.0).clip(0.0, 100.0)
    capacity = pd.to_numeric(sites["capacity_liters"], errors="coerce").fillna(0.0).clip(lower=0.0)
    liters = capacity * fill / 100.0
    return (liters * DENSITY_KG_PER_L).astype(float).tolist(), (liters / 1000.0).astype(float).tolist()


def _truck_shift(truck: Truck, params: SolverParams) -> float:
    return float(truck.shift_seconds or params.shift_duration_s)


def _truck_service(truck: Truck, params: SolverParams) -> float:
    return float(truck.service_seconds_per_site or params.service_time_s)


def _v2_metrics(
    path: list[int],
    loads: list[float],
    matrix: DistanceMatrix,
    truck: Truck,
    params: SolverParams,
    landfill_node: int,
) -> tuple[float, float, float, int, float]:
    distance = sum(matrix.meters[a][b] for a, b in zip(path[:-1], path[1:]))
    duration = sum(matrix.seconds[a][b] for a, b in zip(path[:-1], path[1:]))
    segment_load = 0.0
    max_segment = 0.0
    total_load = 0.0
    dumps = 0
    for node in path[1:-1]:
        if node == landfill_node:
            dumps += 1
            segment_load = 0.0
            duration += params.landfill_service_s
        else:
            load = loads[node - 1]
            total_load += load
            segment_load += load
            max_segment = max(max_segment, segment_load)
            duration += _truck_service(truck, params)
    return distance, duration, total_load, dumps, max_segment


def _insertion_cost(path: list[int], node: int, matrix: DistanceMatrix) -> tuple[float, int]:
    best = (float("inf"), 0)
    for position, (before, after) in enumerate(zip(path[:-1], path[1:])):
        extra = matrix.meters[before][node] + matrix.meters[node][after] - matrix.meters[before][after]
        candidate = (max(0.0, float(extra)), position)
        if candidate < best:
            best = candidate
    return best


def _preflight_v2(
    sites: pd.DataFrame,
    red_nodes: list[int],
    loads: list[float],
    trucks: Sequence[Truck],
    matrix: DistanceMatrix,
    params: SolverParams,
) -> list[str]:
    violations: list[str] = []
    max_capacity = max(truck.capacity_kg for truck in trucks)
    total_capacity = sum(truck.capacity_kg for truck in trucks) * (params.max_dump_trips + 1)
    red_load = sum(loads[node - 1] for node in red_nodes)
    if red_load > total_capacity + 1e-9:
        violations.append(
            "Требуется дополнительная машина или рейс: обязательный объём "
            f"{red_load:.0f} кг превышает доступные {total_capacity:.0f} кг."
        )
    for node in red_nodes:
        site_id = str(sites.iloc[node - 1]["_site_id"])
        if loads[node - 1] > max_capacity + 1e-9:
            violations.append(
                f"Площадка {site_id}: загрузка {loads[node - 1]:.0f} кг больше вместимости "
                f"крупнейшей машины {max_capacity:.0f} кг."
            )
        reachable = any(
            matrix.seconds[0][node]
            + _truck_service(truck, params)
            + matrix.seconds[node][0]
            <= _truck_shift(truck, params)
            for truck in trucks
        )
        if not reachable:
            violations.append(
                f"Площадка {site_id} недостижима в пределах смены; увеличьте смену или смените депо."
            )
    return violations


def _v2_empty_plan(
    violations: list[str], matrix: DistanceMatrix, unserved_red: list[str], mode: str
) -> Plan:
    return Plan(
        routes=[],
        violations=violations,
        dropped_site_ids=[],
        total_distance_m=0.0,
        total_duration_s=0.0,
        distance_source=matrix.source,
        fallback_used=matrix.fallback_used,
        unserved_red=unserved_red,
        mode=mode,
    )


def _plan_v2(sites_df: pd.DataFrame, trucks: Sequence[Truck], params: SolverParams) -> Plan:
    if not trucks:
        raise ValueError("at least one truck is required")
    if any(truck.capacity_kg <= 0 for truck in trucks):
        raise ValueError("every truck needs a positive capacity_kg")
    sites = _v2_frame(sites_df)
    loads, volumes = _v2_loads_and_volumes(sites)
    points = [params.depot] + [
        (float(lat), float(lon)) for lat, lon in zip(sites["_lat"], sites["_lon"])
    ]
    landfill = params.landfill or params.depot
    points.append(landfill)
    landfill_node = len(points) - 1
    matrix = params.matrix or get_matrix(points)
    if len(matrix.seconds) != len(points):
        raise ValueError(
            f"injected V2 matrix has {len(matrix.seconds)} rows, expected {len(points)} "
            "([depot] + sites + [landfill])"
        )

    red_nodes = [index + 1 for index, value in enumerate(sites["klass"]) if value == "RED"]
    yellow_nodes = [index + 1 for index, value in enumerate(sites["klass"]) if value == "YELLOW"]
    red_ids = [str(sites.iloc[node - 1]["_site_id"]) for node in red_nodes]
    mode = "reds_only" if params.reds_only else "predictive"
    violations = _preflight_v2(sites, red_nodes, loads, trucks, matrix, params)
    if violations:
        return _v2_empty_plan(violations, matrix, red_ids, mode)

    pass_one = _solve_v2_ortools(
        sites,
        loads,
        trucks,
        matrix,
        params,
        yellow_penalties={node: 0 for node in yellow_nodes},
    )
    if pass_one is None:
        return _v2_empty_plan(
            ["Требуется дополнительная машина или рейс: RED-план не помещается в смену."],
            matrix,
            red_ids,
            mode,
        )
    red_routes, red_paths, red_served = _extract_v2_solution(
        *pass_one, sites, loads, trucks, matrix, params, landfill_node
    )
    missing_red = [
        str(sites.iloc[node - 1]["_site_id"]) for node in red_nodes if node not in red_served
    ]
    if missing_red:
        return _v2_empty_plan(
            [f"Обязательные площадки не обслужены: {', '.join(missing_red)}."],
            matrix,
            missing_red,
            mode,
        )
    red_volume = sum(volumes[node - 1] for node in red_nodes)
    red_distance = sum(route.distance_m for route in red_routes)
    reference_cost = (
        red_distance / red_volume if red_volume > 1e-12 else params.fallback_cost_per_m3_m
    )
    if params.reds_only or not yellow_nodes:
        return Plan(
            routes=red_routes,
            violations=[],
            dropped_site_ids=[str(sites.iloc[node - 1]["_site_id"]) for node in yellow_nodes],
            total_distance_m=red_distance,
            total_duration_s=sum(route.duration_s for route in red_routes),
            distance_source=matrix.source,
            fallback_used=matrix.fallback_used,
            unserved_red=[],
            reference_cost_m_per_m3=reference_cost,
            mode=mode,
        )

    penalties = {
        node: volumes[node - 1] * reference_cost * params.yellow_tolerance
        for node in yellow_nodes
    }
    pass_two = _solve_v2_ortools(sites, loads, trucks, matrix, params, penalties)
    if pass_two is None:
        return _v2_empty_plan(
            ["Требуется дополнительная машина или рейс: полный план не найден."],
            matrix,
            red_ids,
            mode,
        )
    routes, paths, served_nodes = _extract_v2_solution(
        *pass_two, sites, loads, trucks, matrix, params, landfill_node
    )
    missing_red = [
        str(sites.iloc[node - 1]["_site_id"]) for node in red_nodes if node not in served_nodes
    ]
    if missing_red:
        return _v2_empty_plan(
            [f"Обязательные площадки не обслужены: {', '.join(missing_red)}."],
            matrix,
            missing_red,
            mode,
        )
    served_yellow = [
        str(sites.iloc[node - 1]["_site_id"]) for node in yellow_nodes if node in served_nodes
    ]
    skipped_yellow: list[YellowDecision] = []
    for node in yellow_nodes:
        if node in served_nodes:
            continue
        insertion = min(
            (_insertion_cost(path, node, matrix)[0] for path in paths),
            default=matrix.meters[0][node] + matrix.meters[node][0],
        )
        penalty = penalties[node]
        skipped_yellow.append(
            YellowDecision(
                site_id=str(sites.iloc[node - 1]["_site_id"]),
                insertion_cost_m=insertion,
                penalty_m=penalty,
                volume_m3=volumes[node - 1],
                served=False,
                explanation_ru=(
                    f"детур {insertion / 1000:.1f} км на {volumes[node - 1]:.2f} м³ — "
                    f"дороже лимита {penalty / 1000:.1f} км"
                ),
            )
        )
    dropped = [decision.site_id for decision in skipped_yellow]
    return Plan(
        routes=routes,
        violations=[],
        dropped_site_ids=dropped,
        total_distance_m=sum(route.distance_m for route in routes),
        total_duration_s=sum(route.duration_s for route in routes),
        distance_source=matrix.source,
        fallback_used=matrix.fallback_used,
        served_yellow=served_yellow,
        skipped_yellow=skipped_yellow,
        unserved_red=[],
        reference_cost_m_per_m3=reference_cost,
        mode=mode,
    )


def _solve_v2_ortools(
    sites: pd.DataFrame,
    loads: list[float],
    trucks: Sequence[Truck],
    matrix: DistanceMatrix,
    params: SolverParams,
    yellow_penalties: dict[int, float],
):
    """Solve one V2 pass with vehicle-specific, optional landfill reset nodes."""
    n_sites = len(sites)
    dump_count = len(trucks) * params.max_dump_trips
    node_to_matrix = list(range(n_sites + 1)) + [n_sites + 1] * dump_count
    manager = pywrapcp.RoutingIndexManager(len(node_to_matrix), len(trucks), 0)
    routing = pywrapcp.RoutingModel(manager)
    meters_int = [[int(round(value)) for value in row] for row in matrix.meters]

    def distance_cb(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        a = node_to_matrix[from_node]
        b = node_to_matrix[manager.IndexToNode(to_index)]
        dump_service_equivalent = (
            ceil(params.landfill_service_s * 25.0 / 3.6) if from_node > n_sites else 0
        )
        return meters_int[a][b] + dump_service_equivalent

    distance_index = routing.RegisterTransitCallback(distance_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(distance_index)

    max_capacity = max(floor(truck.capacity_kg) for truck in trucks)
    demands = [0] + [ceil(load) for load in loads] + [-max_capacity] * dump_count

    def demand_cb(from_index: int) -> int:
        return demands[manager.IndexToNode(from_index)]

    routing.AddDimensionWithVehicleCapacity(
        routing.RegisterUnaryTransitCallback(demand_cb),
        max_capacity,
        [floor(truck.capacity_kg) for truck in trucks],
        True,
        "Load",
    )
    load_dimension = routing.GetDimensionOrDie("Load")
    for node in range(n_sites + 1):
        index = manager.NodeToIndex(node)
        if index >= 0:
            routing.solver().Add(load_dimension.SlackVar(index) == 0)

    def dump_count_cb(from_index: int) -> int:
        return int(manager.IndexToNode(from_index) > n_sites)

    routing.AddDimensionWithVehicleCapacity(
        routing.RegisterUnaryTransitCallback(dump_count_cb),
        0,
        [params.max_dump_trips] * len(trucks),
        True,
        "DumpCount",
    )

    def time_cb(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        a = node_to_matrix[from_node]
        b = node_to_matrix[manager.IndexToNode(to_index)]
        service = 0
        if 1 <= from_node <= n_sites:
            service = ceil(params.service_time_s)
        elif from_node > n_sites:
            service = ceil(params.landfill_service_s)
        return ceil(matrix.seconds[a][b]) + service

    routing.AddDimension(
        routing.RegisterTransitCallback(time_cb),
        0,
        max(floor(_truck_shift(truck, params)) for truck in trucks),
        True,
        "Time",
    )
    time_dimension = routing.GetDimensionOrDie("Time")
    for vehicle, truck in enumerate(trucks):
        time_dimension.CumulVar(routing.End(vehicle)).SetMax(
            floor(_truck_shift(truck, params))
        )

    for node, penalty in yellow_penalties.items():
        index = manager.NodeToIndex(node)
        if penalty <= 0:
            routing.ActiveVar(index).SetValue(0)
        else:
            routing.AddDisjunction([index], int(round(penalty)))
    for offset in range(n_sites + 1, len(node_to_matrix)):
        index = manager.NodeToIndex(offset)
        routing.AddDisjunction([index], 0)

    search = pywrapcp.DefaultRoutingSearchParameters()
    search.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    )
    search.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search.time_limit.FromMilliseconds(max(50, int(params.time_limit_s * 1000)))
    solution = routing.SolveWithParameters(search)
    if solution is None:
        return None
    return solution, manager, routing, node_to_matrix


def _extract_v2_solution(
    solution,
    manager,
    routing,
    node_to_matrix: list[int],
    sites: pd.DataFrame,
    loads: list[float],
    trucks: Sequence[Truck],
    matrix: DistanceMatrix,
    params: SolverParams,
    landfill_node: int,
) -> tuple[list[Route], list[list[int]], set[int]]:
    routes: list[Route] = []
    paths: list[list[int]] = []
    served: set[int] = set()
    n_sites = len(sites)
    for vehicle, truck in enumerate(trucks):
        routing_nodes = [0]
        index = routing.Start(vehicle)
        while not routing.IsEnd(index):
            index = solution.Value(routing.NextVar(index))
            routing_nodes.append(manager.IndexToNode(index) if not routing.IsEnd(index) else 0)
        if len(routing_nodes) == 2:
            continue
        path = [
            node if 1 <= node <= n_sites else landfill_node
            for node in routing_nodes
        ]
        path[0] = path[-1] = 0
        site_nodes = [node for node in routing_nodes[1:-1] if 1 <= node <= n_sites]
        served.update(site_nodes)
        distance, duration, total_load, dumps, max_segment = _v2_metrics(
            path, loads, matrix, truck, params, landfill_node
        )
        paths.append(path)
        routes.append(
            Route(
                truck_id=truck.truck_id,
                site_ids=[str(sites.iloc[node - 1]["_site_id"]) for node in site_nodes],
                load_kg=total_load,
                duration_s=duration,
                distance_m=distance,
                dump_stops=dumps,
                ordered_stops=[
                    "LANDFILL"
                    if node > n_sites
                    else str(sites.iloc[node - 1]["_site_id"])
                    for node in routing_nodes[1:-1]
                ],
                max_segment_load_kg=max_segment,
            )
        )
    return routes, paths, served


def _solve(
    sites: pd.DataFrame,
    loads: list[float],
    must_serve: list[bool],
    trucks: Sequence[Truck],
    matrix: DistanceMatrix,
    params: SolverParams,
):
    n_nodes = len(sites) + 1
    manager = pywrapcp.RoutingIndexManager(n_nodes, len(trucks), 0)
    routing = pywrapcp.RoutingModel(manager)

    # Integerize conservatively: demands and transit times round up, capacities
    # and the shift round down, so a solver-feasible plan is float-feasible too.
    meters_int = [[int(round(value)) for value in row] for row in matrix.meters]
    demands_int = [0] + [ceil(load) for load in loads]
    capacities_int = [floor(truck.capacity_kg) for truck in trucks]
    service_int = ceil(params.service_time_s)
    time_int = [
        [ceil(value) + (service_int if i > 0 else 0) for value in row]
        for i, row in enumerate(matrix.seconds)
    ]

    def distance_cb(from_index: int, to_index: int) -> int:
        return meters_int[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]

    def time_cb(from_index: int, to_index: int) -> int:
        return time_int[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]

    def demand_cb(from_index: int) -> int:
        return demands_int[manager.IndexToNode(from_index)]

    distance_cb_index = routing.RegisterTransitCallback(distance_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(distance_cb_index)
    routing.AddDimensionWithVehicleCapacity(
        routing.RegisterUnaryTransitCallback(demand_cb), 0, capacities_int, True, "Load"
    )
    routing.AddDimension(
        routing.RegisterTransitCallback(time_cb), 0, floor(params.shift_duration_s), True, "Time"
    )

    # Any single-site detour costs at most twice the largest arc, so this
    # penalty guarantees optional sites are dropped only when constraints bind.
    drop_penalty = 2 * max(max(row) for row in meters_int) + 1
    for position, mandatory in enumerate(must_serve):
        if not mandatory:
            routing.AddDisjunction([manager.NodeToIndex(position + 1)], drop_penalty)

    search = pywrapcp.DefaultRoutingSearchParameters()
    # Insertion-based construction: unlike PATH_CHEAPEST_ARC it reliably finds
    # a first solution when demand far exceeds capacity and most optional
    # sites must be dropped.
    search.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    )
    search.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search.time_limit.FromMilliseconds(int(params.time_limit_s * 1000))

    return routing.SolveWithParameters(search), manager, routing


def _extract_plan(
    solution,
    manager,
    routing,
    site_ids: list[str],
    loads: list[float],
    trucks: Sequence[Truck],
    matrix: DistanceMatrix,
    params: SolverParams,
) -> Plan:
    routes: list[Route] = []
    violations: list[str] = []
    served: set[int] = set()

    for vehicle, truck in enumerate(trucks):
        node_path = [0]
        index = routing.Start(vehicle)
        while not routing.IsEnd(index):
            index = solution.Value(routing.NextVar(index))
            node_path.append(manager.IndexToNode(index) if not routing.IsEnd(index) else 0)
        stop_nodes = node_path[1:-1]
        if not stop_nodes:
            continue

        served.update(stop_nodes)
        load_kg = sum(loads[node - 1] for node in stop_nodes)
        distance_m = sum(matrix.meters[a][b] for a, b in zip(node_path[:-1], node_path[1:]))
        duration_s = (
            sum(matrix.seconds[a][b] for a, b in zip(node_path[:-1], node_path[1:]))
            + params.service_time_s * len(stop_nodes)
        )
        routes.append(
            Route(
                truck_id=truck.truck_id,
                site_ids=[site_ids[node - 1] for node in stop_nodes],
                load_kg=load_kg,
                duration_s=duration_s,
                distance_m=distance_m,
            )
        )
        # Belt and braces: conservative rounding should make these impossible.
        if load_kg > truck.capacity_kg + 1e-6:
            violations.append(
                f"{truck.truck_id} is loaded to {load_kg:.0f} kg, above its "
                f"{truck.capacity_kg:.0f} kg capacity."
            )
        if duration_s > params.shift_duration_s + 1e-6:
            violations.append(
                f"{truck.truck_id} route takes {duration_s / 3600:.1f} h, above the "
                f"{params.shift_duration_s / 3600:.1f} h shift."
            )

    dropped = [site_ids[node - 1] for node in range(1, len(site_ids) + 1) if node not in served]
    return Plan(
        routes=routes,
        violations=violations,
        dropped_site_ids=dropped,
        total_distance_m=sum(route.distance_m for route in routes),
        total_duration_s=sum(route.duration_s for route in routes),
        distance_source=matrix.source,
        fallback_used=matrix.fallback_used,
    )
