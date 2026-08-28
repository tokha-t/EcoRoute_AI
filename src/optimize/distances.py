"""Travel-time and road-distance matrices for route planning.

Primary source is the committed road artifact in ``data/road_cache``. A local
OSRM server (setup: docs/osrm-setup.md) is the development fallback. If neither
has an answer, matrices use a labelled haversine estimate and geometry returns
per-segment straight fallbacks so the UI can render them dashed and warn.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Sequence

import requests

from src.config import DETOUR_FACTOR
from src.optimize.road_cache import ROAD_CACHE_DIR, load_road_cache

Point = tuple[float, float]  # (latitude, longitude)

OSRM_BASE_URL = "http://localhost:5000"
DEFAULT_MODE = "driving"
OSRM_TIMEOUT_SECONDS = 5.0
FALLBACK_SPEED_KMH = 25.0  # mirrors savings.AVERAGE_TRUCK_SPEED_KMH
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_ROOT / "data" / "cache"
EARTH_RADIUS_M = 6_371_000.0
COORDINATE_PRECISION = 6  # ~0.1 m; keeps cache keys and point lookups stable


class OSRMTableError(RuntimeError):
    """OSRM responded, but not with a usable /table payload."""


@dataclass(frozen=True)
class DistanceMatrix:
    seconds: list[list[float]]
    meters: list[list[float]]
    fallback_used: bool
    source: str  # "road_cache" | "osrm" | "haversine" | "trivial"


@dataclass(frozen=True)
class RouteGeometry:
    points: list[Point]
    source: str  # "road_cache" | "osrm" | "straight" | "mixed"
    segments: list["RouteSegment"] = field(default_factory=list)

    @property
    def segment_sources(self) -> list[str]:
        return [segment.source for segment in self.segments]


@dataclass(frozen=True)
class RouteSegment:
    start: Point
    end: Point
    points: list[Point]
    source: str  # "road_cache" | "osrm" | "straight"
    distance_m: float


def haversine_meters(a: Point, b: Point) -> float:
    lat1, lon1 = radians(a[0]), radians(a[1])
    lat2, lon2 = radians(b[0]), radians(b[1])
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(h))


def _rounded_points(points: Sequence[Point]) -> list[Point]:
    return [
        (round(float(lat), COORDINATE_PRECISION), round(float(lon), COORDINATE_PRECISION))
        for lat, lon in points
    ]


def _fallback_pair(a: Point, b: Point) -> tuple[float, float]:
    meters = haversine_meters(a, b) * DETOUR_FACTOR
    seconds = meters / (FALLBACK_SPEED_KMH / 3.6)
    return seconds, meters


def _fallback_matrices(points: list[Point]) -> tuple[list[list[float]], list[list[float]]]:
    seconds = [[0.0] * len(points) for _ in points]
    meters = [[0.0] * len(points) for _ in points]
    for i, a in enumerate(points):
        for j, b in enumerate(points):
            if i < j:
                pair_seconds, pair_meters = _fallback_pair(a, b)
                seconds[i][j] = seconds[j][i] = pair_seconds
                meters[i][j] = meters[j][i] = pair_meters
    return seconds, meters


def _cache_key(points: list[Point], mode: str) -> str:
    payload = json.dumps({"mode": mode, "points": points}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_path(cache_dir: Path, points: list[Point], mode: str) -> Path:
    return cache_dir / f"osrm_{mode}_{_cache_key(points, mode)[:16]}.json"


def _read_cache(path: Path, size: int) -> tuple[list[list[float]], list[list[float]]] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        seconds, meters = payload["seconds"], payload["meters"]
    except (OSError, ValueError, KeyError):
        return None
    if len(seconds) != size or len(meters) != size:
        return None
    return seconds, meters


def _write_cache(
    path: Path,
    points: list[Point],
    mode: str,
    seconds: list[list[float]],
    meters: list[list[float]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"mode": mode, "points": points, "seconds": seconds, "meters": meters}
    path.write_text(json.dumps(payload), encoding="utf-8")


def _clean_matrix(
    raw: list[list[float | None]],
    points: list[Point],
    fallback_index: int,
) -> list[list[float]]:
    """Replace null entries (unroutable pairs) with the haversine fallback value."""
    cleaned = []
    for i, row in enumerate(raw):
        cleaned_row = []
        for j, value in enumerate(row):
            if value is None:
                value = _fallback_pair(points[i], points[j])[fallback_index]
            cleaned_row.append(float(value))
        cleaned.append(cleaned_row)
    return cleaned


def _osrm_table(
    points: list[Point],
    mode: str,
    base_url: str,
    timeout: float,
) -> tuple[list[list[float]], list[list[float]]]:
    coords = ";".join(f"{lon:.{COORDINATE_PRECISION}f},{lat:.{COORDINATE_PRECISION}f}" for lat, lon in points)
    response = requests.get(
        f"{base_url}/table/v1/{mode}/{coords}",
        params={"annotations": "duration,distance"},
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise OSRMTableError("OSRM returned invalid JSON") from exc

    if payload.get("code") != "Ok":
        raise OSRMTableError(f"OSRM /table returned code {payload.get('code')!r}")
    durations, distances = payload.get("durations"), payload.get("distances")
    if (
        not isinstance(durations, list)
        or not isinstance(distances, list)
        or len(durations) != len(points)
        or len(distances) != len(points)
        or any(len(row) != len(points) for row in durations + distances)
    ):
        raise OSRMTableError("OSRM /table matrices are missing or misshapen")

    seconds = _clean_matrix(durations, points, fallback_index=0)
    meters = _clean_matrix(distances, points, fallback_index=1)
    return seconds, meters


def get_matrix(
    points: Sequence[Point],
    mode: str = DEFAULT_MODE,
    *,
    base_url: str = OSRM_BASE_URL,
    cache_dir: Path = CACHE_DIR,
    road_cache_dir: Path | None = ROAD_CACHE_DIR,
    timeout: float = OSRM_TIMEOUT_SECONDS,
) -> DistanceMatrix:
    """Return travel seconds and road meters between all point pairs.

    Cache holds OSRM results only, so a fallback answer never masks a later
    successful OSRM run. Trivial inputs (fewer than 2 points) skip OSRM.
    """
    rounded = _rounded_points(points)
    if len(rounded) < 2:
        zeros = [[0.0] * len(rounded) for _ in rounded]
        return DistanceMatrix(
            seconds=zeros, meters=[row[:] for row in zeros], fallback_used=False, source="trivial"
        )

    road_cache = load_road_cache(road_cache_dir) if road_cache_dir is not None else None
    if road_cache is not None:
        cached = road_cache.matrix_for(rounded)
        if cached is not None:
            return DistanceMatrix(
                seconds=cached[0],
                meters=cached[1],
                fallback_used=False,
                source="road_cache",
            )

    cache_file = _cache_path(cache_dir, rounded, mode)
    cached = _read_cache(cache_file, len(rounded))
    if cached is not None:
        return DistanceMatrix(seconds=cached[0], meters=cached[1], fallback_used=False, source="osrm")

    try:
        seconds, meters = _osrm_table(rounded, mode, base_url, timeout)
    except (requests.RequestException, OSRMTableError):
        seconds, meters = _fallback_matrices(rounded)
        return DistanceMatrix(seconds=seconds, meters=meters, fallback_used=True, source="haversine")

    _write_cache(cache_file, rounded, mode, seconds, meters)
    return DistanceMatrix(seconds=seconds, meters=meters, fallback_used=False, source="osrm")


def get_route_geometry(
    points: Sequence[Point],
    mode: str = DEFAULT_MODE,
    *,
    base_url: str = OSRM_BASE_URL,
    road_cache_dir: Path | None = ROAD_CACHE_DIR,
    timeout: float = 0.75,
) -> RouteGeometry:
    """Resolve every route edge via cache, live OSRM, then an explicit straight fallback."""
    rounded = _rounded_points(points)
    if len(rounded) < 2:
        return RouteGeometry(rounded, "straight", [])
    road_cache = load_road_cache(road_cache_dir) if road_cache_dir is not None else None
    segments: list[RouteSegment] = []
    for start, end in zip(rounded[:-1], rounded[1:]):
        cached_points = road_cache.geometry_for(start, end) if road_cache is not None else None
        if cached_points:
            segments.append(
                RouteSegment(
                    start,
                    end,
                    cached_points,
                    "road_cache",
                    float(road_cache.distance_for(start, end) or 0.0),
                )
            )
            continue
        coords = f"{start[1]:.6f},{start[0]:.6f};{end[1]:.6f},{end[0]:.6f}"
        try:
            response = requests.get(
                f"{base_url}/route/v1/{mode}/{coords}",
                params={"overview": "full", "geometries": "geojson", "steps": "false"},
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            raw = payload["routes"][0]["geometry"]["coordinates"]
            segment_points = [(float(lonlat[1]), float(lonlat[0])) for lonlat in raw]
            distance_m = float(payload["routes"][0].get("distance", 0.0))
            if len(segment_points) < 2:
                raise ValueError("empty route geometry")
            segments.append(RouteSegment(start, end, segment_points, "osrm", distance_m))
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
            segments.append(
                RouteSegment(
                    start,
                    end,
                    [start, end],
                    "straight",
                    _fallback_pair(start, end)[1],
                )
            )
    joined: list[Point] = []
    for segment in segments:
        joined.extend(segment.points if not joined else segment.points[1:])
    sources = {segment.source for segment in segments}
    source = next(iter(sources)) if len(sources) == 1 else "mixed"
    return RouteGeometry(joined, source, segments)


def _route_coords(route_points: Sequence[dict]) -> list[Point]:
    return _rounded_points([(point["latitude"], point["longitude"]) for point in route_points])


def _route_length_km(coords: list[Point], index: dict[Point, int], meters: list[list[float]]) -> float:
    total_m = sum(meters[index[a]][index[b]] for a, b in zip(coords[:-1], coords[1:]))
    return total_m / 1000.0


def apply_road_distances(
    route_comparison: dict,
    mode: str = DEFAULT_MODE,
    *,
    base_url: str = OSRM_BASE_URL,
    cache_dir: Path = CACHE_DIR,
    timeout: float = OSRM_TIMEOUT_SECONDS,
) -> dict:
    """Recompute distances in a routing.compare_routes() result using get_matrix().

    Routes themselves are untouched (algorithm swap is the next milestone);
    only the reported distances change. Adds "distance_source" and
    "fallback_used" keys for the UI badge.
    """
    routes = {
        "fixed": _route_coords(route_comparison["route_points_fixed"]),
        "greedy": _route_coords(route_comparison["route_points_greedy"]),
        "optimized": _route_coords(route_comparison["route_points_optimized"]),
    }
    unique_points: list[Point] = []
    index: dict[Point, int] = {}
    for coords in routes.values():
        for point in coords:
            if point not in index:
                index[point] = len(unique_points)
                unique_points.append(point)

    matrix = get_matrix(unique_points, mode, base_url=base_url, cache_dir=cache_dir, timeout=timeout)

    fixed_km = _route_length_km(routes["fixed"], index, matrix.meters)
    greedy_km = _route_length_km(routes["greedy"], index, matrix.meters)
    optimized_km = _route_length_km(routes["optimized"], index, matrix.meters)
    distance_saved = max(0.0, fixed_km - optimized_km)
    two_opt_improvement = max(0.0, greedy_km - optimized_km)

    updated = dict(route_comparison)
    updated.update(
        {
            "fixed_route_distance_km": round(fixed_km, 3),
            "selected_greedy_distance_km": round(greedy_km, 3),
            "selected_optimized_distance_km": round(optimized_km, 3),
            "distance_saved_km": round(distance_saved, 3),
            "distance_saved_percent": round((distance_saved / fixed_km * 100) if fixed_km else 0.0, 2),
            "two_opt_improvement_km": round(two_opt_improvement, 3),
            "two_opt_improvement_percent": round(
                (two_opt_improvement / greedy_km * 100) if greedy_km else 0.0, 2
            ),
            "distance_source": matrix.source,
            "fallback_used": matrix.fallback_used,
        }
    )
    return updated
