"""OR-Tools CVRP solver for the daily collection plan (SPEC_V1 6.3).

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

Landfill mid-route dumps and multi-day planning are out of scope (P1).
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor
from typing import Sequence

import pandas as pd
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from src.optimize.distances import DistanceMatrix, Point, get_matrix

# Mixed residential waste runs ~0.10-0.15 kg/L uncompacted; 0.12 (=120 kg/m3)
# is the middle of that band. Calibrate per district with weighbridge data (V2).
DENSITY_KG_PER_L = 0.12

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


@dataclass(frozen=True)
class SolverParams:
    depot: Point  # (latitude, longitude)
    shift_duration_s: float = DEFAULT_SHIFT_DURATION_S
    service_time_s: float = DEFAULT_SERVICE_TIME_S
    time_limit_s: float = DEFAULT_SOLVER_TIME_LIMIT_S
    # Inject a precomputed matrix (rows ordered [depot] + sites_df rows) to
    # skip the OSRM/network path; None means call get_matrix().
    matrix: DistanceMatrix | None = None


@dataclass(frozen=True)
class Route:
    truck_id: str
    site_ids: list[str]  # visit order, depot start/end implicit
    load_kg: float
    duration_s: float
    distance_m: float


@dataclass(frozen=True)
class Plan:
    routes: list[Route]
    violations: list[str]  # must be empty for the plan to be renderable
    dropped_site_ids: list[str]
    total_distance_m: float
    total_duration_s: float
    distance_source: str  # "osrm" | "haversine" | "none"
    fallback_used: bool


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
