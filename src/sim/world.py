"""Deterministic synthetic Baikonur collection world on real OSM geometry."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.config import (
    BASE_RATE,
    WEEKDAY_FACTOR_COMMERCIAL,
    WEEKDAY_FACTOR_RESIDENTIAL,
)
from src.geo.overpass import (
    boundary_bbox,
    load_baikonur_boundary,
    point_in_boundary,
    point_in_ring,
    query_overpass,
)
from src.optimize.distances import haversine_meters

DEFAULT_SITE_COUNT = 250
MIN_SITE_SPACING_M = 60.0
CAPACITY_OPTIONS = (120, 240, 660, 1100)
SECTOR_PLACE_TYPES = {"suburb", "neighbourhood", "quarter"}
RESIDENTIAL_RANK_BUILDINGS = {"apartments", "residential", "house"}


@dataclass(frozen=True)
class SectorBoundary:
    """A named residential catchment with an explicit GeoJSON-style outer ring."""

    name: str
    ring: tuple[tuple[float, float], ...]  # (lon, lat), closed
    residential_buildings: int
    anchor_ids: tuple[str, ...]
    residential_landuse_ids: tuple[int, ...]

    def contains(self, point: tuple[float, float]) -> bool:
        return point_in_ring(point, [list(coordinate) for coordinate in self.ring])

    def as_feature(self) -> dict[str, Any]:
        return {
            "type": "Feature",
            "properties": {
                "name": self.name,
                "source": "OpenStreetMap residential landuse catchment",
                "method": "convex hull of residential landuse polygons assigned to a named OSM place",
                "residential_buildings": self.residential_buildings,
                "anchor_ids": list(self.anchor_ids),
                "residential_landuse_ids": list(self.residential_landuse_ids),
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[list(coordinate) for coordinate in self.ring]],
            },
        }


def _bbox_text(bbox: tuple[float, float, float, float]) -> str:
    return ",".join(str(value) for value in bbox)


def build_world_query(bbox: tuple[float, float, float, float]) -> str:
    bounds = _bbox_text(bbox)
    return f"""
[out:json][timeout:40];
(
  nwr[amenity~"^(waste_disposal|recycling)$"]({bounds});
  nwr[building]({bounds});
  nwr[shop]({bounds});
  nwr[office]({bounds});
);
out center tags;
nwr[place~"^(suburb|neighbourhood|quarter)$"]({bounds});
out center tags geom;
way[landuse="residential"]({bounds});
out center tags geom;
way[highway~"^(residential|living_street)$"]({bounds});
out tags geom;
""".strip()


def weekday_factor(area_type: str, day: int) -> float:
    factors = WEEKDAY_FACTOR_COMMERCIAL if area_type == "commercial" else WEEKDAY_FACTOR_RESIDENTIAL
    return float(factors[day % 7])


def _coordinate(element: dict) -> tuple[float, float] | None:
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center", {})
    if "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    geometry = element.get("geometry") or []
    if geometry:
        return (
            sum(float(point["lat"]) for point in geometry) / len(geometry),
            sum(float(point["lon"]) for point in geometry) / len(geometry),
        )
    return None


def _deduplicate_elements(elements: list[dict]) -> list[dict]:
    """Merge repeated Overpass records while retaining the richest geometry."""
    merged: dict[tuple[str, int], dict] = {}
    for element in elements:
        key = (str(element.get("type", "")), int(element.get("id", -1)))
        previous = merged.get(key)
        if previous is None:
            merged[key] = element
            continue
        candidate = {**previous, **element}
        candidate["tags"] = {**previous.get("tags", {}), **element.get("tags", {})}
        if len(previous.get("geometry") or []) > len(element.get("geometry") or []):
            candidate["geometry"] = previous["geometry"]
        merged[key] = candidate
    return list(merged.values())


def _closed_ring(element: dict) -> list[list[float]] | None:
    geometry = element.get("geometry") or []
    ring = [[float(point["lon"]), float(point["lat"])] for point in geometry]
    if len(ring) < 4:
        return None
    if tuple(ring[0]) != tuple(ring[-1]):
        return None
    return ring


def _convex_hull(points: Iterable[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    """Return a deterministic closed convex hull for ``(lon, lat)`` points."""
    unique = sorted(set(points))
    if len(unique) < 3:
        raise ValueError("A sector boundary requires at least three distinct points")

    def cross(
        origin: tuple[float, float],
        first: tuple[float, float],
        second: tuple[float, float],
    ) -> float:
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (
            first[1] - origin[1]
        ) * (second[0] - origin[0])

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    hull = tuple(lower[:-1] + upper[:-1])
    return (*hull, hull[0])


def _place_anchors(elements: list[dict]) -> list[tuple[float, float, str, str]]:
    anchors: list[tuple[float, float, str, str]] = []
    for element in elements:
        tags = element.get("tags", {})
        coordinate = _coordinate(element)
        if tags.get("place") not in SECTOR_PLACE_TYPES or not tags.get("name") or coordinate is None:
            continue
        anchor_id = f"{element.get('type', 'element')}/{element.get('id', '')}"
        anchors.append((*coordinate, str(tags["name"]), anchor_id))
    return anchors


def build_sector_candidates(
    elements: list[dict],
    boundary: dict[str, Any],
) -> list[SectorBoundary]:
    """Build named residential catchments and rank them by residential buildings."""
    anchors = _place_anchors(elements)
    if not anchors:
        raise ValueError("OSM response contains no named suburb/neighbourhood/quarter anchors")

    grouped_rings: dict[str, list[list[list[float]]]] = {}
    grouped_ids: dict[str, list[int]] = {}
    grouped_anchors: dict[str, set[str]] = {}
    named = [(lat, lon, name) for lat, lon, name, _ in anchors]
    anchor_ids_by_name: dict[str, set[str]] = {}
    for _, _, name, anchor_id in anchors:
        anchor_ids_by_name.setdefault(name, set()).add(anchor_id)

    for element in elements:
        if element.get("tags", {}).get("landuse") != "residential":
            continue
        ring = _closed_ring(element)
        if ring is None:
            continue
        # Only source polygons wholly inside the authoritative district contribute.
        if not all(point_in_boundary((lat, lon), boundary) for lon, lat in ring[:-1]):
            continue
        centroid = (
            sum(lat for _, lat in ring[:-1]) / (len(ring) - 1),
            sum(lon for lon, _ in ring[:-1]) / (len(ring) - 1),
        )
        name = _nearest_name(centroid, named)
        grouped_rings.setdefault(name, []).append(ring)
        grouped_ids.setdefault(name, []).append(int(element.get("id", -1)))
        grouped_anchors.setdefault(name, set()).update(anchor_ids_by_name[name])

    candidates: list[SectorBoundary] = []
    for name, rings in grouped_rings.items():
        hull = _convex_hull(
            (float(lon), float(lat))
            for ring in rings
            for lon, lat in ring[:-1]
        )
        residential_count = 0
        for element in elements:
            if element.get("tags", {}).get("building") not in RESIDENTIAL_RANK_BUILDINGS:
                continue
            coordinate = _coordinate(element)
            if (
                coordinate is not None
                and point_in_boundary(coordinate, boundary)
                and point_in_ring(coordinate, [list(point) for point in hull])
            ):
                residential_count += 1
        candidates.append(
            SectorBoundary(
                name=name,
                ring=hull,
                residential_buildings=residential_count,
                anchor_ids=tuple(sorted(grouped_anchors[name])),
                residential_landuse_ids=tuple(sorted(grouped_ids[name])),
            )
        )
    if not candidates:
        raise ValueError("OSM response contains no residential landuse polygons inside the district")
    return sorted(
        candidates,
        key=lambda candidate: (-candidate.residential_buildings, candidate.name),
    )


def select_sector_boundary(
    elements: list[dict],
    boundary: dict[str, Any],
    requested: str | None = None,
) -> SectorBoundary:
    candidates = build_sector_candidates(elements, boundary)
    if requested is None:
        return candidates[0]
    matches = [candidate for candidate in candidates if candidate.name.casefold() == requested.casefold()]
    if not matches:
        available = ", ".join(candidate.name for candidate in candidates)
        raise ValueError(f"Unknown OSM residential sector {requested!r}; available sectors: {available}")
    return matches[0]


def _nearest_name(point: tuple[float, float], named: list[tuple[float, float, str]]) -> str:
    return min(named, key=lambda item: haversine_meters(point, (item[0], item[1])))[2]


def _select_sector(
    places: list[tuple[float, float, str]],
    elements: list[dict],
    requested: str | None,
) -> str:
    """Select an explicit sector or the one with most residential buildings."""
    names = sorted({name for _, _, name in places})
    if requested:
        matches = [name for name in names if name.casefold() == requested.casefold()]
        if not matches:
            raise ValueError(f"Unknown OSM sector {requested!r}; available sectors: {', '.join(names)}")
        return matches[0]
    residential_count = {name: 0 for name in names}
    for element in elements:
        tags = element.get("tags", {})
        if tags.get("building") not in RESIDENTIAL_RANK_BUILDINGS:
            continue
        coord = _coordinate(element)
        if coord is not None:
            residential_count[_nearest_name(coord, places)] += 1
    return max(names, key=lambda name: (residential_count[name], name))


def _road_candidates(roads: list[dict], rng: np.random.Generator) -> Iterable[tuple[float, float, str]]:
    order = rng.permutation(len(roads))
    while True:
        emitted = False
        for index in order:
            road = roads[int(index)]
            geometry = road.get("geometry") or []
            if len(geometry) < 2:
                continue
            emitted = True
            start_index = int(rng.integers(0, len(geometry) - 1))
            a, b = geometry[start_index], geometry[start_index + 1]
            fraction = float(rng.random())
            yield (
                float(a["lat"]) + fraction * (float(b["lat"]) - float(a["lat"])),
                float(a["lon"]) + fraction * (float(b["lon"]) - float(a["lon"])),
                str(road.get("tags", {}).get("name", "")),
            )
        if not emitted:
            return


def _area_type(point: tuple[float, float], features: list[tuple[float, float, dict]]) -> str:
    nearby = [tags for lat, lon, tags in features if haversine_meters(point, (lat, lon)) <= 180]
    commercial = sum(
        bool(tags.get("shop") or tags.get("office") or tags.get("building") == "commercial")
        for tags in nearby
    )
    apartments = sum(tags.get("building") in {"apartments", "residential"} for tags in nearby)
    houses = sum(tags.get("building") in {"house", "detached"} for tags in nearby)
    if commercial > apartments + houses:
        return "commercial"
    if apartments and houses:
        return "mixed"
    if apartments:
        return "multistorey"
    if houses:
        return "private"
    if commercial:
        return "commercial"
    return "mixed"


def _initial_last_service(fill_pct: float, daily_rate: float) -> int:
    return -min(3, max(0, int(fill_pct / max(daily_rate, 1.0))))


def generate_world(
    seed: int = 42,
    n_sites: int = DEFAULT_SITE_COUNT,
    bbox: tuple[float, float, float, float] | None = None,
    *,
    payload: dict | None = None,
    boundary: dict[str, Any] | None = None,
    sector: str | None = None,
) -> pd.DataFrame:
    """Generate one polygon-filtered pilot sector; identical inputs are deterministic."""
    if n_sites < 1:
        raise ValueError("n_sites must be positive")
    boundary = boundary or load_baikonur_boundary()
    bbox = bbox or boundary_bbox(boundary)
    payload = payload or query_overpass(build_world_query(bbox))
    raw_elements = _deduplicate_elements(list(payload.get("elements", [])))
    selected_sector = select_sector_boundary(raw_elements, boundary, sector)
    elements = [
        element
        for element in raw_elements
        if (coord := _coordinate(element)) is not None and point_in_boundary(coord, boundary)
    ]

    def in_sector(element: dict) -> bool:
        coord = _coordinate(element)
        return coord is not None and selected_sector.contains(coord)

    sector_elements = [element for element in elements if in_sector(element)]
    roads = [
        element
        for element in elements
        if element.get("tags", {}).get("highway") in {"residential", "living_street"}
        and element.get("geometry")
        and any(
            selected_sector.contains((float(point["lat"]), float(point["lon"])))
            for point in element["geometry"]
        )
    ]
    waste = [
        element
        for element in sector_elements
        if element.get("tags", {}).get("amenity") in {"waste_disposal", "recycling"}
    ]
    features = [
        (*_coordinate(element), element.get("tags", {}))
        for element in sector_elements
        if element.get("tags", {}).get("building")
        or element.get("tags", {}).get("shop")
        or element.get("tags", {}).get("office")
    ]
    addressed = [
        (
            *_coordinate(element),
            str(element.get("tags", {}).get("addr:street")),
            str(element.get("tags", {}).get("addr:housenumber", "")),
        )
        for element in sector_elements
        if element.get("tags", {}).get("addr:street")
    ]
    if not roads and len(waste) < n_sites:
        raise ValueError(
            f"OSM residential sector {selected_sector.name!r} contains no residential street geometry for top-up"
        )

    rng = np.random.default_rng(seed)
    named_roads = []
    for road in roads:
        name = road.get("tags", {}).get("name")
        coord = _coordinate(road)
        if name and coord:
            named_roads.append((*coord, str(name)))
    chosen: list[tuple[float, float, str, bool, dict]] = []
    for element in sorted(waste, key=lambda item: (item.get("type", ""), item.get("id", 0))):
        coord = _coordinate(element)
        if coord:
            tags = element.get("tags", {})
            chosen.append((*coord, str(tags.get("addr:street", "")), True, tags))

    candidates = _road_candidates(roads, rng)
    attempts = 0
    while len(chosen) < n_sites and attempts < n_sites * 1000:
        attempts += 1
        try:
            lat, lon, street = next(candidates)
        except StopIteration as exc:
            raise ValueError("Not enough OSM street geometry to generate the requested world") from exc
        point = (lat, lon)
        if (
            point_in_boundary(point, boundary)
            and selected_sector.contains(point)
            and all(haversine_meters(point, (row[0], row[1])) >= MIN_SITE_SPACING_M for row in chosen)
        ):
            chosen.append((lat, lon, street, False, {}))
    if len(chosen) < n_sites:
        raise ValueError(f"Could place only {len(chosen)} sites at least {MIN_SITE_SPACING_M:.0f} m apart")

    rows = []
    for number, (lat, lon, street, source_real, tags) in enumerate(chosen, start=1):
        point = (lat, lon)
        district = selected_sector.name
        if tags.get("addr:street"):
            street = str(tags["addr:street"])
        if not street and named_roads:
            street = _nearest_name(point, named_roads)
        house = str(tags.get("addr:housenumber", ""))
        if addressed and not tags.get("addr:street"):
            _, _, nearest_street, nearest_house = min(
                addressed,
                key=lambda item: haversine_meters(point, (item[0], item[1])),
            )
            street, house = nearest_street, nearest_house
        address = f"ул. {street}{', ' + house if house else ''}, район {district}" if street else district
        area = _area_type(point, features)
        containers = int(rng.integers(1, 7))
        container_liters = int(rng.choice(CAPACITY_OPTIONS, p=[0.08, 0.17, 0.30, 0.45]))
        capacity = containers * container_liters
        noise = float(rng.lognormal(mean=0.0, sigma=0.22))
        rate = float(np.clip(BASE_RATE[area] * (1000 / capacity) ** 0.5 * noise, 3, 95))
        fill = float(rng.uniform(0, 85))
        rows.append(
            {
                "site_id": f"SITE-{number:04d}",
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "address": address,
                "district": district,
                "sector": selected_sector.name,
                "containers": containers,
                "container_liters": container_liters,
                "capacity_liters": capacity,
                "area_type": area,
                "daily_fill_rate_pct": round(rate, 4),
                "fill_pct": round(fill, 4),
                "last_service_day": _initial_last_service(fill, rate),
                "source_real": source_real,
            }
        )
    world = pd.DataFrame(rows)
    inside_count = sum(
        selected_sector.contains((float(row.lat), float(row.lon)))
        for row in world.itertuples(index=False)
    )
    world.attrs["sector_feature"] = selected_sector.as_feature()
    world.attrs["sector_scope"] = {
        "validated": inside_count / len(world) >= 0.95,
        "sector": selected_sector.name,
        "sites_inside_polygon": inside_count,
        "site_count": len(world),
        "containment_pct": inside_count / len(world) * 100,
        "method": selected_sector.as_feature()["properties"]["method"],
    }
    return world


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/world.csv"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sites", type=int, default=DEFAULT_SITE_COUNT)
    parser.add_argument("--sector", help="Exact OSM suburb/neighbourhood name")
    parser.add_argument(
        "--sector-out",
        type=Path,
        default=Path("data/cache/osm/selected_sector.geojson"),
    )
    parser.add_argument("--metadata-out", type=Path, default=Path("data/world.meta.json"))
    args = parser.parse_args()
    world = generate_world(seed=args.seed, n_sites=args.sites, sector=args.sector)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    world.to_csv(args.out, index=False)
    args.sector_out.parent.mkdir(parents=True, exist_ok=True)
    args.sector_out.write_text(
        json.dumps(world.attrs["sector_feature"], ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    from src.optimize.road_cache import world_hash

    args.metadata_out.parent.mkdir(parents=True, exist_ok=True)
    args.metadata_out.write_text(
        json.dumps(
            {
                "world_hash": world_hash(world),
                "sector_scope": world.attrs["sector_scope"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    real = int(world["source_real"].sum())
    print(
        f"Wrote {len(world)} sites in sector {world.iloc[0]['sector']} to {args.out}: "
        f"{real} real OSM, {len(world) - real} synthesized"
    )


if __name__ == "__main__":
    _main()
