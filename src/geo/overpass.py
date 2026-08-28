"""Small cached Overpass client used by the V2 synthetic-world generator."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "osm"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT_SECONDS = 45.0
BAIKONUR_BOUNDARY_CACHE = CACHE_DIR / "baikonur_boundary.geojson"
ASTANA_QUERY_BBOX = (51.0, 71.1, 51.4, 71.8)
_BAIKONUR_NAME = re.compile(r"байқоңыр|байконур", re.IGNORECASE)


class OverpassUnavailable(RuntimeError):
    """Neither Overpass nor a cached response could satisfy a query."""


class BoundaryUnavailable(RuntimeError):
    """A real Baikonur administrative polygon could not be loaded."""


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


def build_baikonur_boundary_query(
    bbox: tuple[float, float, float, float] = ASTANA_QUERY_BBOX,
) -> str:
    """Query the named district, preferring levels 9/10 but accepting OSM's current level."""
    bounds = ",".join(str(value) for value in bbox)
    return f"""
[out:json][timeout:90];
(
  relation["boundary"="administrative"]["admin_level"~"9|10"]
    ["name"~"Байқоңыр|Байконур"]({bounds});
  relation["boundary"="administrative"]
    ["name"~"Байқоңыр|Байконур"]({bounds});
  relation["boundary"="administrative"]
    ["name:ru"~"Байконур"]({bounds});
);
out geom;
""".strip()


def _point_key(point: tuple[float, float]) -> tuple[float, float]:
    return round(point[0], 7), round(point[1], 7)


def _stitch_rings(segments: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Join Overpass member-way geometry into every closed relation ring."""
    remaining = [segment[:] for segment in segments if len(segment) >= 2]
    rings: list[list[tuple[float, float]]] = []
    while remaining:
        chain = remaining.pop(0)
        while _point_key(chain[0]) != _point_key(chain[-1]):
            joined = False
            for index, segment in enumerate(remaining):
                if _point_key(segment[0]) == _point_key(chain[-1]):
                    chain.extend(segment[1:])
                elif _point_key(segment[-1]) == _point_key(chain[-1]):
                    chain.extend(reversed(segment[:-1]))
                elif _point_key(segment[-1]) == _point_key(chain[0]):
                    chain = segment[:-1] + chain
                elif _point_key(segment[0]) == _point_key(chain[0]):
                    chain = list(reversed(segment[1:])) + chain
                else:
                    continue
                remaining.pop(index)
                joined = True
                break
            if not joined:
                raise BoundaryUnavailable("Baikonur boundary relation contains an open outer ring")
        if len(chain) < 4:
            raise BoundaryUnavailable("Baikonur boundary relation contains a degenerate ring")
        rings.append(chain)
    return rings


def point_in_ring(point: tuple[float, float], ring: list[list[float]]) -> bool:
    """Ray-casting point-in-ring for ``(lat, lon)`` against GeoJSON coordinates."""
    lat, lon = point
    inside = False
    for first, second in zip(ring, ring[1:] + ring[:1]):
        x1, y1 = float(first[0]), float(first[1])
        x2, y2 = float(second[0]), float(second[1])
        if (y1 > lat) == (y2 > lat):
            continue
        crossing_lon = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
        if lon < crossing_lon:
            inside = not inside
    return inside


def point_in_boundary(point: tuple[float, float], boundary: dict[str, Any]) -> bool:
    """Return whether a point is in any outer polygon and outside all of its holes."""
    geometry = boundary.get("geometry", boundary)
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    polygons = [coordinates] if geometry_type == "Polygon" else coordinates
    if geometry_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("boundary must be a GeoJSON Polygon or MultiPolygon")
    return any(
        polygon
        and point_in_ring(point, polygon[0])
        and not any(point_in_ring(point, hole) for hole in polygon[1:])
        for polygon in polygons
    )


def boundary_bbox(boundary: dict[str, Any]) -> tuple[float, float, float, float]:
    geometry = boundary.get("geometry", boundary)
    coordinates = geometry.get("coordinates", [])
    polygons = [coordinates] if geometry.get("type") == "Polygon" else coordinates
    points = [point for polygon in polygons for ring in polygon for point in ring]
    if not points:
        raise ValueError("boundary contains no coordinates")
    return (
        min(float(point[1]) for point in points),
        min(float(point[0]) for point in points),
        max(float(point[1]) for point in points),
        max(float(point[0]) for point in points),
    )


def boundary_from_overpass(payload: dict) -> dict[str, Any]:
    """Convert the matching OSM boundary relation into GeoJSON MultiPolygon."""
    relations = []
    for element in payload.get("elements", []):
        if element.get("type") != "relation":
            continue
        tags = element.get("tags", {})
        names = " ".join(str(tags.get(key, "")) for key in ("name", "name:kk", "name:ru", "alt_name:ru"))
        if tags.get("boundary") == "administrative" and _BAIKONUR_NAME.search(names):
            relations.append(element)
    if not relations:
        raise BoundaryUnavailable(
            "Overpass returned no Baikonur administrative relation; refusing bbox fallback"
        )
    relation = max(relations, key=lambda item: len(item.get("members", [])))
    role_segments: dict[str, list[list[tuple[float, float]]]] = {"outer": [], "inner": []}
    for member in relation.get("members", []):
        role = str(member.get("role") or "outer")
        if role not in role_segments or member.get("type") != "way":
            continue
        geometry = member.get("geometry") or []
        segment = [(float(point["lat"]), float(point["lon"])) for point in geometry]
        if segment:
            role_segments[role].append(segment)
    outer_rings = _stitch_rings(role_segments["outer"])
    inner_rings = _stitch_rings(role_segments["inner"])
    if not outer_rings:
        raise BoundaryUnavailable("Baikonur administrative relation has no outer geometry")

    polygons: list[list[list[list[float]]]] = [[[[lon, lat] for lat, lon in ring]] for ring in outer_rings]
    for inner in inner_rings:
        inner_geojson = [[lon, lat] for lat, lon in inner]
        for polygon in polygons:
            if point_in_ring(inner[0], polygon[0]):
                polygon.append(inner_geojson)
                break
        else:
            raise BoundaryUnavailable("Baikonur boundary contains an unassigned inner ring")
    feature: dict[str, Any] = {
        "type": "Feature",
        "properties": {
            "osm_relation_id": int(relation["id"]),
            "name": str(relation.get("tags", {}).get("name", "Байқоңыр ауданы")),
            "admin_level": str(relation.get("tags", {}).get("admin_level", "")),
            "source": "OpenStreetMap via Overpass",
        },
        "geometry": {"type": "MultiPolygon", "coordinates": polygons},
    }
    feature["properties"]["bbox"] = list(boundary_bbox(feature))
    return feature


def load_baikonur_boundary(
    *,
    cache_path: Path = BAIKONUR_BOUNDARY_CACHE,
    payload: dict | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Load the real multi-part boundary, using GeoJSON offline and never a bbox."""
    if payload is None and cache_path.exists() and not refresh:
        try:
            boundary = json.loads(cache_path.read_text(encoding="utf-8"))
            boundary_bbox(boundary)
            return boundary
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise BoundaryUnavailable(f"Invalid cached Baikonur boundary: {cache_path}") from exc
    try:
        source = payload or query_overpass(build_baikonur_boundary_query())
        boundary = boundary_from_overpass(source)
    except (OverpassUnavailable, BoundaryUnavailable) as exc:
        if cache_path.exists():
            try:
                boundary = json.loads(cache_path.read_text(encoding="utf-8"))
                boundary_bbox(boundary)
                return boundary
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as cache_exc:
                raise BoundaryUnavailable(
                    f"Baikonur boundary fetch failed and cache is invalid: {cache_path}"
                ) from cache_exc
        raise BoundaryUnavailable(
            "Baikonur administrative polygon is unavailable and no GeoJSON cache exists. "
            "Connect once and run `python -m src.sim.world`; bbox fallback is forbidden."
        ) from exc
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(boundary, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return boundary
