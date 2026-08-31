"""Load and validate the committed V2 road-distance artifact once per process."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from src.geo.polyline import decode_polyline

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROAD_CACHE_DIR = PROJECT_ROOT / "data" / "road_cache"
COORDINATE_PRECISION = 6
Point = tuple[float, float]


class RoadCacheError(RuntimeError):
    """The road artifact is missing, stale, or internally inconsistent."""


def coordinate_key(point: Point) -> tuple[float, float]:
    return round(float(point[0]), COORDINATE_PRECISION), round(float(point[1]), COORDINATE_PRECISION)


def world_hash(world: pd.DataFrame) -> str:
    """Stable hash of site identity and coordinates only."""
    required = ["site_id", "lat", "lon"]
    missing = [column for column in required if column not in world]
    if missing:
        raise ValueError(f"world is missing hash columns: {missing}")
    rows = [
        [str(row.site_id), *coordinate_key((row.lat, row.lon))]
        for row in world[required].sort_values("site_id").itertuples(index=False)
    ]
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def routing_profile(meta: dict[str, Any]) -> str:
    """Return the profile the committed graph was built with, never an implied default."""
    value = str(meta.get("osrm_profile") or "").strip()
    return value or "unknown"


def courtyard_access_text(meta: dict[str, Any], lang: str = "ru") -> str:
    """Format the >40 m OSRM snap audit stored by the cache builder."""
    audit = meta.get("courtyard_access")
    if not isinstance(audit, dict):
        return (
            "покрытие внутридворовых подъездов не измерено"
            if lang == "ru"
            else "courtyard access coverage was not measured"
        )
    count = int(audit.get("sites_without_mapped_access", 0))
    share = float(audit.get("share_pct", 0.0))
    if lang == "ru":
        return f"площадок без картированного подъезда: {count} ({share:.1f}%)"
    return f"sites without mapped access: {count} ({share:.1f}%)"


@dataclass(frozen=True)
class RoadCache:
    directory: Path
    meta: dict[str, Any]
    nodes: pd.DataFrame
    meters: np.ndarray
    seconds: np.ndarray
    coordinate_to_index: dict[tuple[float, float], int]
    geometries: dict[tuple[int, int], str]

    def indices_for(self, points: Sequence[Point]) -> list[int] | None:
        try:
            return [self.coordinate_to_index[coordinate_key(point)] for point in points]
        except KeyError:
            return None

    def matrix_for(self, points: Sequence[Point]) -> tuple[list[list[float]], list[list[float]]] | None:
        indices = self.indices_for(points)
        if indices is None:
            return None
        positions = np.ix_(indices, indices)
        return self.seconds[positions].astype(float).tolist(), self.meters[positions].astype(float).tolist()

    def geometry_for(self, start: Point, end: Point) -> list[Point] | None:
        indices = self.indices_for([start, end])
        if indices is None:
            return None
        encoded = self.geometries.get((indices[0], indices[1]))
        return decode_polyline(encoded) if encoded is not None else None

    def distance_for(self, start: Point, end: Point) -> float | None:
        indices = self.indices_for([start, end])
        return None if indices is None else float(self.meters[indices[0], indices[1]])

    def validate_world(self, world: pd.DataFrame, depot: Point, landfill: Point) -> None:
        actual_hash = world_hash(world)
        if self.meta.get("world_hash") != actual_hash:
            raise RoadCacheError(
                "Road cache world_hash does not match data/world.csv; rebuild data/road_cache"
            )
        for kind, point in (("depot", depot), ("landfill", landfill)):
            matching = self.nodes[self.nodes["kind"].eq(kind)]
            expected = {coordinate_key((row.lat, row.lon)) for row in matching.itertuples()}
            if coordinate_key(point) not in expected:
                raise RoadCacheError(f"{kind} lies outside the road cache; rebuild it before routing")


def _load_geometry(path: Path) -> dict[tuple[int, int], str]:
    geometries: dict[tuple[int, int], str] = {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                record = json.loads(line)
                geometries[(int(record["a"]), int(record["b"]))] = str(record["poly"])
            except (ValueError, KeyError, TypeError) as exc:
                raise RoadCacheError(f"Invalid geometry record at line {line_number}") from exc
    return geometries


@lru_cache(maxsize=4)
def load_road_cache(directory: Path = ROAD_CACHE_DIR) -> RoadCache | None:
    """Load the road artifact once; return ``None`` only when it is wholly absent."""
    directory = Path(directory)
    required = {
        "meta": directory / "meta.json",
        "nodes": directory / "nodes.csv",
        "matrix": directory / "matrix.npz",
        "geometry": directory / "geometry.jsonl.gz",
    }
    existing = {name: path.exists() for name, path in required.items()}
    if not any(existing.values()):
        return None
    missing = [name for name, exists in existing.items() if not exists]
    if missing:
        raise RoadCacheError(f"Incomplete road cache, missing: {', '.join(missing)}")
    try:
        meta = json.loads(required["meta"].read_text(encoding="utf-8"))
        nodes = pd.read_csv(required["nodes"])
        with np.load(required["matrix"]) as matrix:
            meters = np.asarray(matrix["meters"], dtype=np.float32)
            seconds = np.asarray(matrix["seconds"], dtype=np.float32)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise RoadCacheError(f"Cannot load road cache from {directory}") from exc
    count = len(nodes)
    if meters.shape != (count, count) or seconds.shape != (count, count):
        raise RoadCacheError("Road cache matrix dimensions do not match nodes.csv")
    if int(meta.get("node_count", -1)) != count:
        raise RoadCacheError("Road cache meta.node_count does not match nodes.csv")
    coordinate_to_index = {
        coordinate_key((row.lat, row.lon)): int(row.index)
        for row in nodes.reset_index().itertuples(index=False)
    }
    if len(coordinate_to_index) != count:
        raise RoadCacheError("Road cache contains duplicate node coordinates")
    return RoadCache(
        directory=directory,
        meta=meta,
        nodes=nodes,
        meters=meters,
        seconds=seconds,
        coordinate_to_index=coordinate_to_index,
        geometries=_load_geometry(required["geometry"]),
    )


def clear_road_cache_memory() -> None:
    load_road_cache.cache_clear()
