from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.geo.infrastructure import feature_point, load_real_landfill


def payload() -> dict:
    return {
        "elements": [
            {
                "type": "way",
                "id": 10,
                "tags": {"landuse": "landfill", "name": "Тестовый полигон"},
                "geometry": [
                    {"lat": 51.0, "lon": 71.0},
                    {"lat": 51.0, "lon": 71.2},
                    {"lat": 51.2, "lon": 71.2},
                    {"lat": 51.2, "lon": 71.0},
                    {"lat": 51.0, "lon": 71.0},
                ],
            }
        ]
    }


def test_real_landfill_centroid_is_cached_for_offline_use(tmp_path: Path) -> None:
    cache = tmp_path / "landfill.geojson"
    feature = load_real_landfill((51.1, 71.1), cache_path=cache, payload=payload())
    assert feature_point(feature) == pytest.approx((51.1, 71.1))
    with patch("src.geo.infrastructure.query_overpass", side_effect=AssertionError("network")):
        assert load_real_landfill((51.1, 71.1), cache_path=cache) == feature
