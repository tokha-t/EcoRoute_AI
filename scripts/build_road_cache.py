#!/usr/bin/env python3
"""Build the committed road cache from a fixed world and a reachable OSRM."""

from __future__ import annotations

import argparse
import gzip
import json
import re
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import DEPOT_COORDS, LANDFILL_COORDS  # noqa: E402
from src.geo.polyline import encode_polyline  # noqa: E402
from src.optimize.distances import DistanceMatrix  # noqa: E402
from src.optimize.road_cache import ROAD_CACHE_DIR, world_hash  # noqa: E402
from src.optimize.solver import Truck  # noqa: E402
from src.sim.fill import ClassificationParams  # noqa: E402
from src.sim.trajectory import TrajectoryParams, build_trajectory  # noqa: E402

MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 30.0
COURTYARD_ACCESS_THRESHOLD_M = 40.0
PROFILE_PATTERN = re.compile(r"^[a-z0-9_-]+$")


def _coordinates(nodes: pd.DataFrame) -> str:
    return ";".join(f"{row.lon:.6f},{row.lat:.6f}" for row in nodes.itertuples())


def _fetch_matrix(
    nodes: pd.DataFrame,
    base_url: str,
    profile: str,
) -> tuple[np.ndarray, np.ndarray]:
    response = requests.get(
        f"{base_url.rstrip('/')}/table/v1/{profile}/{_coordinates(nodes)}",
        params={"annotations": "duration,distance"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "Ok":
        raise RuntimeError(f"OSRM table failed: {payload.get('message') or payload.get('code')}")
    if any(
        value is None
        for matrix in (payload["durations"], payload["distances"])
        for row in matrix
        for value in row
    ):
        raise RuntimeError("OSRM returned an unroutable matrix cell; approximation is forbidden")
    return np.asarray(payload["distances"], dtype=np.float32), np.asarray(
        payload["durations"], dtype=np.float32
    )


def _geometry_edges(meters: np.ndarray, k: int) -> list[tuple[int, int]]:
    count = meters.shape[0]
    edges: set[tuple[int, int]] = set()
    for start in range(count):
        nearest = np.argsort(meters[start])
        for end in nearest:
            end = int(end)
            if end != start:
                edges.add((start, end))
            if sum(1 for a, _ in edges if a == start) >= k:
                break
    landfill = count - 1
    for node in range(count):
        if node == 0 or node == landfill:
            continue
        edges.update({(0, node), (node, 0), (landfill, node), (node, landfill)})
    edges.update({(0, landfill), (landfill, 0)})
    return sorted(edges)


def _reference_route_edges(
    world: pd.DataFrame,
    nodes: pd.DataFrame,
    meters: np.ndarray,
    seconds: np.ndarray,
) -> set[tuple[int, int]]:
    """Edges used by the deterministic default 30-day dispatcher trajectory."""
    matrix = DistanceMatrix(seconds.tolist(), meters.tolist(), False, "road_cache")
    params = TrajectoryParams(
        trucks=tuple(Truck(f"TRUCK-{number}", 5_000, 8 * 3600) for number in range(1, 5)),
        classification=ClassificationParams(),
    )
    with tempfile.TemporaryDirectory(prefix="road-cache-trajectory-") as cache_dir:
        snapshots = build_trajectory(
            world,
            params,
            days=30,
            seed=42,
            cache_dir=Path(cache_dir),
            matrix=matrix,
        )
    indices = {str(row.node_id): int(row.Index) for row in nodes.itertuples()}
    route_edges: set[tuple[int, int]] = set()
    for snapshot in snapshots:
        for route in snapshot.plan.routes:
            ordered = ["DEPOT", *route.ordered_stops, "DEPOT"]
            for start, end in zip(ordered[:-1], ordered[1:]):
                edge = (indices[start], indices[end])
                if edge[0] != edge[1]:
                    route_edges.add(edge)
    return route_edges


def _fetch_geometry(
    edge: tuple[int, int],
    nodes: pd.DataFrame,
    base_url: str,
    profile: str,
) -> dict[str, int | str]:
    start, end = edge
    a, b = nodes.iloc[start], nodes.iloc[end]
    coordinates = f"{a.lon:.6f},{a.lat:.6f};{b.lon:.6f},{b.lat:.6f}"
    response = requests.get(
        f"{base_url.rstrip('/')}/route/v1/{profile}/{coordinates}",
        params={"overview": "full", "geometries": "geojson", "steps": "false"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RuntimeError(f"OSRM route failed for edge {start}->{end}")
    lon_lat = payload["routes"][0]["geometry"]["coordinates"]
    points = [(float(lat), float(lon)) for lon, lat in lon_lat]
    return {"a": start, "b": end, "poly": encode_polyline(points)}


def _fetch_access_distance(row: object, base_url: str, profile: str) -> float:
    response = requests.get(
        f"{base_url.rstrip('/')}/nearest/v1/{profile}/{float(row.lon):.6f},{float(row.lat):.6f}",
        params={"number": 1},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    waypoints = payload.get("waypoints") or []
    if payload.get("code") != "Ok" or not waypoints:
        raise RuntimeError(f"OSRM nearest failed for {row.node_id}")
    distance = waypoints[0].get("distance")
    if distance is None or float(distance) < 0:
        raise RuntimeError(f"OSRM nearest omitted snap distance for {row.node_id}")
    return float(distance)


def _courtyard_access(
    nodes: pd.DataFrame,
    base_url: str,
    profile: str,
    workers: int,
) -> dict[str, float | int]:
    sites = list(nodes[nodes["kind"].eq("site")].itertuples(index=False))
    distances: list[float] = []
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {
            executor.submit(_fetch_access_distance, row, base_url, profile): str(row.node_id)
            for row in sites
        }
        for future in as_completed(pending):
            site_id = pending[future]
            try:
                distances.append(future.result())
            except (requests.RequestException, RuntimeError, ValueError, KeyError) as exc:
                failures.append(f"{site_id}: {exc}")
    if failures:
        raise RuntimeError(
            f"OSRM nearest failed for {len(failures)} site(s); courtyard coverage was not computed"
        )
    without_access = sum(distance > COURTYARD_ACCESS_THRESHOLD_M for distance in distances)
    count = len(distances)
    return {
        "threshold_m": COURTYARD_ACCESS_THRESHOLD_M,
        "site_count": count,
        "sites_without_mapped_access": without_access,
        "share_pct": round(without_access / count * 100, 1) if count else 0.0,
        "mean_snap_distance_m": round(float(np.mean(distances)), 1) if distances else 0.0,
        "max_snap_distance_m": round(max(distances), 1) if distances else 0.0,
    }


def build_cache(
    world_path: Path,
    output_dir: Path,
    base_url: str,
    k: int,
    workers: int,
    profile: str = "driving",
) -> dict:
    if k < 1:
        raise ValueError("k must be positive")
    if not PROFILE_PATTERN.fullmatch(profile):
        raise ValueError("profile must contain only lowercase letters, digits, underscores, or hyphens")
    world = pd.read_csv(world_path)
    sites = world[["site_id", "lat", "lon"]].rename(columns={"site_id": "node_id"})
    sites.insert(1, "kind", "site")
    nodes = pd.concat(
        [
            pd.DataFrame(
                [{"node_id": "DEPOT", "kind": "depot", "lat": DEPOT_COORDS[0], "lon": DEPOT_COORDS[1]}]
            ),
            sites,
            pd.DataFrame(
                [
                    {
                        "node_id": "LANDFILL",
                        "kind": "landfill",
                        "lat": LANDFILL_COORDS[0],
                        "lon": LANDFILL_COORDS[1],
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    try:
        requests.get(
            f"{base_url.rstrip('/')}/nearest/v1/{profile}/{nodes.iloc[0].lon},{nodes.iloc[0].lat}",
            timeout=3,
        ).raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"OSRM is unreachable at {base_url}; road cache was not built") from exc
    meters, seconds = _fetch_matrix(nodes, base_url, profile)
    courtyard_access = _courtyard_access(nodes, base_url, profile, workers)
    print(
        "courtyard access: "
        f"{courtyard_access['sites_without_mapped_access']}/{courtyard_access['site_count']} "
        f"sites exceed {courtyard_access['threshold_m']:.0f} m"
    )
    route_edges = _reference_route_edges(world, nodes, meters, seconds)
    edges = sorted(set(_geometry_edges(meters, k)) | route_edges)
    records: list[dict[str, int | str]] = []
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {
            executor.submit(_fetch_geometry, edge, nodes, base_url, profile): edge for edge in edges
        }
        for number, future in enumerate(as_completed(pending), start=1):
            edge = pending[future]
            try:
                records.append(future.result())
            except (requests.RequestException, RuntimeError, ValueError, KeyError) as exc:
                failures.append(f"{edge[0]}->{edge[1]}: {exc}")
            if number % 500 == 0 or number == len(edges):
                print(f"geometry {number}/{len(edges)}")
    coverage = len(records) / len(edges) * 100 if edges else 100.0
    if coverage < 95:
        raise RuntimeError(
            f"Road geometry coverage is only {coverage:.2f}% ({len(failures)} failures); refusing artifact"
        )
    record_edges = {(int(record["a"]), int(record["b"])) for record in records}
    route_coverage = len(record_edges & route_edges) / len(route_edges) * 100 if route_edges else 100.0
    if route_coverage < 95:
        raise RuntimeError(f"Reference route coverage is only {route_coverage:.2f}%; refusing artifact")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="road-cache-", dir=output_dir.parent))
    try:
        nodes.to_csv(temporary / "nodes.csv", index=False)
        np.savez_compressed(temporary / "matrix.npz", meters=meters, seconds=seconds)
        with gzip.open(temporary / "geometry.jsonl.gz", "wt", encoding="utf-8") as handle:
            for record in sorted(records, key=lambda item: (int(item["a"]), int(item["b"]))):
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        meta = {
            "world_hash": world_hash(world),
            "built_at": datetime.now(UTC).isoformat(),
            "node_count": len(nodes),
            "k": k,
            "osrm_profile": profile,
            "courtyard_access": courtyard_access,
            "coverage_pct": round(route_coverage, 3),
            "geometry_request_coverage_pct": round(coverage, 3),
            "geometry_edges": len(records),
            "requested_geometry_edges": len(edges),
            "reference_route_edges": len(route_edges),
            "depot": list(DEPOT_COORDS),
            "landfill": list(LANDFILL_COORDS),
        }
        (temporary / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        artifact_size = sum(path.stat().st_size for path in temporary.iterdir())
        if artifact_size > MAX_ARTIFACT_BYTES:
            raise RuntimeError(f"Road cache is {artifact_size / 1024 / 1024:.1f} MB, above the 25 MB limit")
        if output_dir.exists():
            shutil.rmtree(output_dir)
        temporary.replace(output_dir)
        print(f"Wrote {output_dir} ({artifact_size / 1024 / 1024:.2f} MB, coverage {coverage:.2f}%)")
        return meta
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", type=Path, default=Path("data/world.csv"))
    parser.add_argument("--output", type=Path, default=ROAD_CACHE_DIR)
    parser.add_argument("--osrm", default="http://localhost:5000")
    parser.add_argument("--k", type=int, default=25)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument(
        "--profile",
        default="driving",
        help="Semantic OSRM graph profile recorded in cache metadata (for example refuse_truck)",
    )
    args = parser.parse_args()
    build_cache(args.world, args.output, args.osrm, args.k, args.workers, args.profile)


if __name__ == "__main__":
    main()
