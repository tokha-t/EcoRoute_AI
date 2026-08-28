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

    def geometry(edge, nodes, base_url):
        return {"a": edge[0], "b": edge[1], "poly": "??"}

    with (
        patch("scripts.build_road_cache.requests.get", return_value=reachable),
        patch("scripts.build_road_cache._fetch_matrix", return_value=(matrix, matrix)),
        patch("scripts.build_road_cache._reference_route_edges", return_value={(0, 1)}),
        patch("scripts.build_road_cache._fetch_geometry", side_effect=geometry),
        patch("scripts.build_road_cache.MAX_ARTIFACT_BYTES", 1),
    ):
        with pytest.raises(RuntimeError, match="above the 25 MB limit"):
            build_cache(write_world(tmp_path), tmp_path / "road_cache", "http://osrm", 1, 1)
    assert not (tmp_path / "road_cache").exists()
