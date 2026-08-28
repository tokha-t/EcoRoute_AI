"""Small cached Overpass client used by the V2 synthetic-world generator."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "osm"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT_SECONDS = 45.0


class OverpassUnavailable(RuntimeError):
    """Neither Overpass nor a cached response could satisfy a query."""


def _cache_path(query: str, cache_dir: Path) -> Path:
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:20]
    return cache_dir / f"overpass_{digest}.json.gz"


def query_overpass(
    query: str,
    *,
    cache_dir: Path = CACHE_DIR,
    url: str = OVERPASS_URL,
    timeout: float = OVERPASS_TIMEOUT_SECONDS,
) -> dict:
    """Return Overpass JSON, preferring live data and falling back to cache."""
    path = _cache_path(query, cache_dir)
    try:
        response = requests.get(
            url,
            params={"data": query},
            headers={
                "Accept": "application/json",
                "User-Agent": "EcoRoute-AI/2.0 (simulation cache builder)",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload.get("elements"), list):
            raise ValueError("missing elements")
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        return payload
    except (requests.RequestException, ValueError, OSError) as exc:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as cache_exc:
            raise OverpassUnavailable(
                "Overpass is unavailable and no cached Baikonur OSM response exists. "
                "Connect once and run `python -m src.sim.world` to populate data/cache/osm/."
            ) from cache_exc
        if not isinstance(payload.get("elements"), list):
            raise OverpassUnavailable(f"Cached Overpass response is invalid: {path}") from exc
        return payload
