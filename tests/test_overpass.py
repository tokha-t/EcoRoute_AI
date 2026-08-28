from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from src.geo.overpass import (
    BoundaryUnavailable,
    boundary_from_overpass,
    load_baikonur_boundary,
    point_in_boundary,
    query_overpass,
)


def boundary_payload() -> dict:
    def member(ref: int, points: list[tuple[float, float]]) -> dict:
        return {
            "type": "way",
            "ref": ref,
            "role": "outer",
            "geometry": [{"lat": lat, "lon": lon} for lat, lon in points],
        }

    return {
        "elements": [
            {
                "type": "relation",
                "id": 8593081,
                "tags": {
                    "boundary": "administrative",
                    "admin_level": "6",
                    "name": "Байқоңыр ауданы",
                },
                "members": [
                    member(1, [(51.20, 71.40), (51.30, 71.40), (51.30, 71.60)]),
                    member(2, [(51.30, 71.60), (51.20, 71.60), (51.20, 71.40)]),
                    member(
                        3,
                        [
                            (51.31, 71.30),
                            (51.33, 71.30),
                            (51.33, 71.32),
                            (51.31, 71.32),
                            (51.31, 71.30),
                        ],
                    ),
                ],
            }
        ]
    }


def test_live_response_is_cached_and_used_offline(tmp_path: Path) -> None:
    payload = {"elements": [{"type": "node", "id": 1}]}
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    with patch("src.geo.overpass.requests.get", return_value=response):
        assert query_overpass("test-query", cache_dir=tmp_path) == payload

    cache_files = list(tmp_path.glob("*.json.gz"))
    assert len(cache_files) == 1
    with patch("src.geo.overpass.requests.get", side_effect=requests.ConnectionError):
        assert query_overpass("test-query", cache_dir=tmp_path) == payload


def test_multi_part_boundary_rejects_city_centre_and_keeps_exclave() -> None:
    boundary = boundary_from_overpass(boundary_payload())
    assert boundary["geometry"]["type"] == "MultiPolygon"
    assert len(boundary["geometry"]["coordinates"]) == 2
    assert not point_in_boundary((51.17, 71.40), boundary)
    assert point_in_boundary((51.32, 71.31), boundary)


def test_boundary_cache_works_offline_and_missing_cache_stops(tmp_path: Path) -> None:
    cache = tmp_path / "baikonur_boundary.geojson"
    expected = load_baikonur_boundary(cache_path=cache, payload=boundary_payload())
    with patch("src.geo.overpass.query_overpass", side_effect=requests.ConnectionError):
        assert load_baikonur_boundary(cache_path=cache) == expected

    with (
        patch("src.geo.overpass.query_overpass", side_effect=BoundaryUnavailable("offline")),
        pytest.raises(BoundaryUnavailable, match="bbox fallback is forbidden"),
    ):
        load_baikonur_boundary(cache_path=tmp_path / "missing.geojson")
