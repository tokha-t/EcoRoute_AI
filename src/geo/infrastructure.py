"""Authoritative cached OSM infrastructure points used by the V2 simulation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.geo.overpass import CACHE_DIR, OverpassUnavailable, query_overpass
from src.optimize.distances import haversine_meters

LANDFILL_CACHE = CACHE_DIR / "landfill.geojson"
ASTANA_INFRASTRUCTURE_BBOX = (50.8, 70.8, 51.6, 72.1)


class InfrastructureUnavailable(RuntimeError):
    """Required real infrastructure could not be fetched or loaded."""


def build_landfill_query(
    bbox: tuple[float, float, float, float] = ASTANA_INFRASTRUCTURE_BBOX,
) -> str:
    bounds = ",".join(str(value) for value in bbox)
    return f"""
[out:json][timeout:90];
(
  nwr["landuse"="landfill"]({bounds});
  nwr["amenity"="waste_transfer_station"]({bounds});
);
out geom center tags;
""".strip()


def _polygon_centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Return a planar centroid as ``(lat, lon)`` for a small OSM polygon."""
    if len(points) < 3:
        raise ValueError("polygon needs at least three points")
    ring = points if points[0] == points[-1] else [*points, points[0]]
    twice_area = centroid_x = centroid_y = 0.0
    for (lat_a, lon_a), (lat_b, lon_b) in zip(ring[:-1], ring[1:]):
        cross = lon_a * lat_b - lon_b * lat_a
        twice_area += cross
        centroid_x += (lon_a + lon_b) * cross
        centroid_y += (lat_a + lat_b) * cross
    if abs(twice_area) < 1e-12:
        return (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )
    return centroid_y / (3 * twice_area), centroid_x / (3 * twice_area)


def _coordinate(element: dict[str, Any]) -> tuple[float, float] | None:
    geometry = element.get("geometry") or []
    if len(geometry) >= 3:
        return _polygon_centroid([(float(point["lat"]), float(point["lon"])) for point in geometry])
    center = element.get("center") or {}
    if "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    return None


def landfill_from_overpass(payload: dict[str, Any], reference: tuple[float, float]) -> dict[str, Any]:
    """Select the nearest real landfill/transfer facility to the pilot sector."""
    candidates = []
    for element in payload.get("elements", []):
        tags = element.get("tags", {})
        if tags.get("landuse") != "landfill" and tags.get("amenity") != "waste_transfer_station":
            continue
        coordinate = _coordinate(element)
        if coordinate is not None:
            candidates.append((haversine_meters(reference, coordinate), element, coordinate))
    if not candidates:
        raise InfrastructureUnavailable("OSM returned no landfill or waste transfer station near Astana")
    _, element, coordinate = min(candidates, key=lambda item: item[0])
    tags = element.get("tags", {})
    return {
        "type": "Feature",
        "properties": {
            "osm_type": str(element.get("type", "")),
            "osm_id": int(element["id"]),
            "name": str(tags.get("name") or "Полигон ТБО"),
            "kind": "waste_transfer_station"
            if tags.get("amenity") == "waste_transfer_station"
            else "landfill",
            "source": "OpenStreetMap via Overpass",
        },
        "geometry": {
            "type": "Point",
            "coordinates": [coordinate[1], coordinate[0]],
        },
    }


def load_real_landfill(
    reference: tuple[float, float],
    *,
    cache_path: Path = LANDFILL_CACHE,
    payload: dict[str, Any] | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Load a real OSM landfill point, preferring the committed offline cache."""
    if payload is None and cache_path.exists() and not refresh:
        try:
            feature = json.loads(cache_path.read_text(encoding="utf-8"))
            if feature.get("geometry", {}).get("type") != "Point":
                raise ValueError("not a point")
            return feature
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise InfrastructureUnavailable(f"Invalid landfill cache: {cache_path}") from exc
    try:
        feature = landfill_from_overpass(payload or query_overpass(build_landfill_query()), reference)
    except (OverpassUnavailable, InfrastructureUnavailable) as exc:
        if cache_path.exists():
            return load_real_landfill(reference, cache_path=cache_path)
        raise InfrastructureUnavailable(
            "A real Astana landfill is unavailable and no OSM cache exists"
        ) from exc
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(feature, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return feature


def feature_point(feature: dict[str, Any]) -> tuple[float, float]:
    lon, lat = feature["geometry"]["coordinates"]
    return float(lat), float(lon)
