from __future__ import annotations

import gzip
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import requests

from src.geo.polyline import encode_polyline
from src.optimize.distances import get_matrix, get_route_geometry
from src.optimize.road_cache import clear_road_cache_memory, load_road_cache, world_hash

DEPOT = (51.1, 71.1)
SITE = (51.2, 71.2)
LANDFILL = (51.3, 71.3)


def write_cache(path: Path) -> pd.DataFrame:
    path.mkdir(exist_ok=True)
    world = pd.DataFrame([{"site_id": "S1", "lat": SITE[0], "lon": SITE[1]}])
    nodes = pd.DataFrame(
        [
            {"node_id": "DEPOT", "kind": "depot", "lat": DEPOT[0], "lon": DEPOT[1]},
            {"node_id": "S1", "kind": "site", "lat": SITE[0], "lon": SITE[1]},
            {
                "node_id": "LANDFILL",
                "kind": "landfill",
                "lat": LANDFILL[0],
                "lon": LANDFILL[1],
            },
        ]
    )
    nodes.to_csv(path / "nodes.csv", index=False)
    meters = np.array([[0, 100, 300], [110, 0, 200], [310, 210, 0]], dtype=np.float32)
    np.savez_compressed(path / "matrix.npz", meters=meters, seconds=meters / 10)
    records = [
        {"a": 0, "b": 1, "poly": encode_polyline([DEPOT, (51.15, 71.12), SITE])},
        {"a": 1, "b": 2, "poly": encode_polyline([SITE, LANDFILL])},
    ]
    with gzip.open(path / "geometry.jsonl.gz", "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    (path / "meta.json").write_text(
        json.dumps(
            {
                "world_hash": world_hash(world),
                "node_count": 3,
                "coverage_pct": 100.0,
            }
        ),
        encoding="utf-8",
    )
    clear_road_cache_memory()
    return world


def test_matrix_and_geometry_use_road_cache_with_osrm_offline(tmp_path: Path) -> None:
    write_cache(tmp_path)
    with patch("src.optimize.distances.requests.get", side_effect=requests.ConnectionError):
        matrix = get_matrix(
            [DEPOT, SITE, LANDFILL],
            cache_dir=tmp_path / "legacy",
            road_cache_dir=tmp_path,
        )
        geometry = get_route_geometry([DEPOT, SITE, LANDFILL], road_cache_dir=tmp_path)
    assert matrix.source == "road_cache"
    assert matrix.meters[0][1] == 100
    assert geometry.source == "road_cache"
    assert geometry.segment_sources == ["road_cache", "road_cache"]
    assert len(geometry.points) > 3


def test_uncovered_geometry_is_counted_as_straight(tmp_path: Path) -> None:
    write_cache(tmp_path)
    with patch("src.optimize.distances.requests.get", side_effect=requests.ConnectionError):
        geometry = get_route_geometry([LANDFILL, DEPOT], road_cache_dir=tmp_path)
    assert geometry.source == "straight"
    assert geometry.segment_sources == ["straight"]
    assert geometry.points == [LANDFILL, DEPOT]


def test_cache_validates_world_hash_and_infrastructure(tmp_path: Path) -> None:
    world = write_cache(tmp_path)
    cache = load_road_cache(tmp_path)
    assert cache is not None
    cache.validate_world(world, DEPOT, LANDFILL)
