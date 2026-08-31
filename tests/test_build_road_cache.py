from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest
import requests

from scripts.build_road_cache import build_cache


def write_world(path: Path) -> Path:
    world_path = path / "world.csv"
    pd.DataFrame([{"site_id": "S1", "lat": 51.2, "lon": 71.4}]).to_csv(world_path, index=False)
    return world_path


def test_build_aborts_when_osrm_is_unreachable(tmp_path: Path) -> None:
    with patch(
        "scripts.build_road_cache.requests.get",
        side_effect=requests.ConnectionError("offline"),
    ):
        with pytest.raises(RuntimeError, match="OSRM is unreachable"):
            build_cache(write_world(tmp_path), tmp_path / "road_cache", "http://offline", 1, 1)
    assert not (tmp_path / "road_cache").exists()


def test_build_aborts_when_artifact_exceeds_size_guard(tmp_path: Path) -> None:
    reachable = Mock()
    reachable.raise_for_status.return_value = None
    matrix = np.ones((3, 3), dtype=np.float32)
    np.fill_diagonal(matrix, 0)

    def geometry(edge, nodes, base_url, profile):
        return {"a": edge[0], "b": edge[1], "poly": "??"}

    with (
        patch("scripts.build_road_cache.requests.get", return_value=reachable),
        patch("scripts.build_road_cache._fetch_matrix", return_value=(matrix, matrix)),
        patch(
            "scripts.build_road_cache._courtyard_access",
            return_value={
                "threshold_m": 40.0,
                "site_count": 1,
                "sites_without_mapped_access": 0,
                "share_pct": 0.0,
                "mean_snap_distance_m": 5.0,
                "max_snap_distance_m": 5.0,
            },
        ),
        patch("scripts.build_road_cache._reference_route_edges", return_value={(0, 1)}),
        patch("scripts.build_road_cache._fetch_geometry", side_effect=geometry),
        patch("scripts.build_road_cache.MAX_ARTIFACT_BYTES", 1),
    ):
        with pytest.raises(RuntimeError, match="above the 25 MB limit"):
            build_cache(write_world(tmp_path), tmp_path / "road_cache", "http://osrm", 1, 1)
    assert not (tmp_path / "road_cache").exists()


def test_build_records_refuse_truck_profile_and_courtyard_coverage(tmp_path: Path) -> None:
    reachable = Mock()
    reachable.raise_for_status.return_value = None
    matrix = np.ones((3, 3), dtype=np.float32)
    np.fill_diagonal(matrix, 0)
    access = {
        "threshold_m": 40.0,
        "site_count": 1,
        "sites_without_mapped_access": 1,
        "share_pct": 100.0,
        "mean_snap_distance_m": 55.0,
        "max_snap_distance_m": 55.0,
    }

    def geometry(edge, nodes, base_url, profile):
        assert profile == "refuse_truck"
        return {"a": edge[0], "b": edge[1], "poly": "??"}

    with (
        patch("scripts.build_road_cache.requests.get", return_value=reachable),
        patch("scripts.build_road_cache._fetch_matrix", return_value=(matrix, matrix)) as matrix_fetch,
        patch("scripts.build_road_cache._courtyard_access", return_value=access),
        patch("scripts.build_road_cache._reference_route_edges", return_value={(0, 1)}),
        patch("scripts.build_road_cache._fetch_geometry", side_effect=geometry),
    ):
        meta = build_cache(
            write_world(tmp_path),
            tmp_path / "road_cache",
            "http://osrm",
            1,
            1,
            "refuse_truck",
        )

    matrix_fetch.assert_called_once()
    assert matrix_fetch.call_args.args[2] == "refuse_truck"
    assert meta["osrm_profile"] == "refuse_truck"
    assert meta["courtyard_access"] == access
