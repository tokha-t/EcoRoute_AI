"""Persistent, deterministic day snapshots for instant V2 simulation navigation."""

from __future__ import annotations

import hashlib
import json
import pickle
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import (
    DEPOT_COORDS,
    FALLBACK_COST_PER_M3_M,
    LANDFILL_COORDS,
    MAX_DUMP_TRIPS,
    SIMULATION_DAYS,
    YELLOW_TOLERANCE,
)
from src.optimize.distances import DistanceMatrix, Point, get_matrix
from src.optimize.solver import Plan, SolverParams, Truck, plan_routes
from src.sim.fill import ClassificationParams, advance_day, classify, empty_sites

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRAJECTORY_CACHE_DIR = PROJECT_ROOT / "data" / "cache"
ProgressCallback = Callable[[int, int], None]
Solver = Callable[[pd.DataFrame, Sequence[Truck], SolverParams], Plan]


class TrajectoryCacheMismatch(RuntimeError):
    """A persisted trajectory does not match its expected inputs."""


@dataclass(frozen=True)
class TrajectoryParams:
    trucks: tuple[Truck, ...]
    classification: ClassificationParams = ClassificationParams()
    depot: Point = DEPOT_COORDS
    landfill: Point = LANDFILL_COORDS
    shift_duration_s: float = 8 * 3600.0
    yellow_tolerance: float = YELLOW_TOLERANCE
    reds_only: bool = False
    max_dump_trips: int = MAX_DUMP_TRIPS
    fallback_cost_per_m3_m: float = FALLBACK_COST_PER_M3_M
    # The fixed solution-count gate makes the trajectory independent of CPU
    # speed; the generous wall-clock cap remains only as a safety valve.
    solver_time_limit_s: float = 5.0
    solver_solution_limit: int = 100


@dataclass(frozen=True)
class DaySnapshot:
    day: int
    state_df: pd.DataFrame
    classified_df: pd.DataFrame
    plan: Plan


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def trajectory_world_hash(world: pd.DataFrame) -> str:
    normalized = world.sort_values("site_id").reset_index(drop=True)
    return _hash_text(normalized.to_csv(index=False, float_format="%.10g"))


def trajectory_params_hash(params: TrajectoryParams) -> str:
    return _hash_text(json.dumps(asdict(params), sort_keys=True, separators=(",", ":")))


def trajectory_key(world: pd.DataFrame, params: TrajectoryParams, days: int, seed: int) -> str:
    payload = {
        "version": 1,
        "world_hash": trajectory_world_hash(world),
        "params_hash": trajectory_params_hash(params),
        "days": int(days),
        "seed": int(seed),
    }
    return _hash_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def trajectory_cache_path(
    world: pd.DataFrame,
    params: TrajectoryParams,
    days: int,
    seed: int,
    cache_dir: Path = TRAJECTORY_CACHE_DIR,
) -> Path:
    return cache_dir / f"trajectory_{trajectory_key(world, params, days, seed)[:20]}.pkl"


def _world_points(world: pd.DataFrame, params: TrajectoryParams) -> list[Point]:
    return (
        [params.depot] + list(zip(world["lat"].astype(float), world["lon"].astype(float))) + [params.landfill]
    )


def _subset_matrix(full: DistanceMatrix, world_indices: Sequence[int], world_size: int) -> DistanceMatrix:
    indices = [0] + [int(index) + 1 for index in world_indices] + [world_size + 1]
    return DistanceMatrix(
        seconds=[[full.seconds[i][j] for j in indices] for i in indices],
        meters=[[full.meters[i][j] for j in indices] for i in indices],
        fallback_used=full.fallback_used,
        source=full.source,
    )


def solve_day(
    state: pd.DataFrame,
    day: int,
    params: TrajectoryParams,
    *,
    matrix: DistanceMatrix | None = None,
    overrides: dict[str, str] | None = None,
    solver: Solver = plan_routes,
) -> tuple[pd.DataFrame, Plan]:
    """Classify and solve one day, applying explicit dispatcher overrides."""
    classified = classify(state, day, params.classification).reset_index(drop=True)
    classified["manual_override"] = ""
    overrides = {str(key): str(value) for key, value in (overrides or {}).items()}
    known = set(classified["site_id"].astype(str))
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise ValueError(f"Unknown override site ids: {', '.join(unknown)}")
    site_ids = classified["site_id"].astype(str)
    include = site_ids.isin({key for key, value in overrides.items() if value == "include"})
    exclude = site_ids.isin({key for key, value in overrides.items() if value == "exclude"})
    classified.loc[include, ["klass", "reason", "manual_override"]] = [
        "RED",
        "manual_include",
        "include",
    ]
    classified.loc[include, "must_serve"] = True
    classified.loc[exclude, "manual_override"] = "exclude"
    candidate_mask = classified["klass"].isin(["RED", "YELLOW"]) & ~exclude
    indices = [int(index) for index in classified.index[candidate_mask]]
    candidates = classified.loc[indices].reset_index(drop=True)
    full_matrix = matrix or get_matrix(_world_points(classified, params), timeout=0.5)
    if candidates.empty:
        plan = Plan(
            [],
            [],
            [],
            0.0,
            0.0,
            full_matrix.source,
            full_matrix.fallback_used,
            mode="reds_only" if params.reds_only else "predictive",
            manual_overrides=overrides,
        )
        return classified, plan
    local = _subset_matrix(full_matrix, indices, len(classified))
    solver_params = SolverParams(
        depot=params.depot,
        landfill=params.landfill,
        matrix=local,
        shift_duration_s=params.shift_duration_s,
        max_dump_trips=params.max_dump_trips,
        fallback_cost_per_m3_m=params.fallback_cost_per_m3_m,
        yellow_tolerance=params.yellow_tolerance,
        reds_only=params.reds_only,
        time_limit_s=params.solver_time_limit_s,
        solution_limit=params.solver_solution_limit,
    )
    plan = solver(candidates, params.trucks, solver_params)
    return classified, replace(plan, manual_overrides=overrides)


def _load_disk_cache(path: Path, expected_key: str) -> list[DaySnapshot] | None:
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            envelope = pickle.load(handle)  # noqa: S301 - local generated cache only
    except (OSError, pickle.UnpicklingError, EOFError) as exc:
        raise TrajectoryCacheMismatch(f"Cannot read trajectory cache {path}") from exc
    if envelope.get("trajectory_key") != expected_key:
        raise TrajectoryCacheMismatch(f"Trajectory hash mismatch in {path}")
    snapshots = envelope.get("snapshots")
    if not isinstance(snapshots, list) or not all(
        isinstance(snapshot, DaySnapshot) for snapshot in snapshots
    ):
        raise TrajectoryCacheMismatch(f"Trajectory payload is invalid in {path}")
    return snapshots


def build_trajectory(
    world: pd.DataFrame,
    params: TrajectoryParams,
    days: int = SIMULATION_DAYS,
    seed: int = 42,
    *,
    cache_dir: Path = TRAJECTORY_CACHE_DIR,
    progress: ProgressCallback | None = None,
    matrix: DistanceMatrix | None = None,
    solver: Solver = plan_routes,
) -> list[DaySnapshot]:
    """Build or load all day snapshots exactly once for a parameter signature."""
    if days < 0:
        raise ValueError("days cannot be negative")
    key = trajectory_key(world, params, days, seed)
    path = trajectory_cache_path(world, params, days, seed, cache_dir)
    cached = _load_disk_cache(path, key)
    if cached is not None:
        return cached
    state = world.copy().reset_index(drop=True)
    full_matrix = matrix or get_matrix(_world_points(state, params), timeout=0.5)
    rng = np.random.default_rng(seed)
    snapshots: list[DaySnapshot] = []
    previous_plan: Plan | None = None
    for day in range(days + 1):
        if day > 0:
            served = [
                site_id
                for route in (previous_plan.routes if previous_plan is not None else [])
                for site_id in route.site_ids
            ]
            state = empty_sites(state, served, day - 1)
            state = advance_day(state, day, rng)
        classified, plan = solve_day(
            state,
            day,
            params,
            matrix=full_matrix,
            solver=solver,
        )
        snapshots.append(DaySnapshot(day, state.copy(), classified.copy(), plan))
        previous_plan = plan
        if progress is not None:
            progress(day, days)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as handle:
        pickle.dump({"trajectory_key": key, "snapshots": snapshots}, handle)
    temporary.replace(path)
    return snapshots


def get_snapshot(snapshots: Sequence[DaySnapshot], day: int) -> DaySnapshot:
    """Pure O(1) day lookup with no solver work."""
    if day < 0 or day >= len(snapshots):
        raise IndexError(f"day {day} is outside trajectory 0..{len(snapshots) - 1}")
    snapshot = snapshots[day]
    if snapshot.day != day:
        raise TrajectoryCacheMismatch("Trajectory day order is inconsistent")
    return snapshot
