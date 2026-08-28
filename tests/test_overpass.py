from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import requests

from src.geo.overpass import query_overpass


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
