"""Deterministic synthetic Baikonur collection world on real OSM geometry."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
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
    query_overpass,
)
from src.optimize.distances import haversine_meters

DEFAULT_SITE_COUNT = 250
MIN_SITE_SPACING_M = 60.0
CAPACITY_OPTIONS = (120, 240, 660, 1100)


def _bbox_text(bbox: tuple[float, float, float, float]) -> str:
    return ",".join(str(value) for value in bbox)


def build_world_query(bbox: tuple[float, float, float, float]) -> str:
    bounds = _bbox_text(bbox)
    return f"""
[out:json][timeout:40];
(
  nwr[amenity~"^(waste_disposal|recycling)$"]({bounds});
  nwr[place~"^(suburb|neighbourhood)$"]({bounds});
  nwr[building]({bounds});
  nwr[shop]({bounds});
  nwr[office]({bounds});
);
out center tags;
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


def _nearest_name(point: tuple[float, float], named: list[tuple[float, float, str]]) -> str:
    return min(named, key=lambda item: haversine_meters(point, (item[0], item[1])))[2]


def _select_sector(
    places: list[tuple[float, float, str]],
    elements: list[dict],
    requested: str | None,
) -> str:
    """Select an explicit sector or the OSM place with the densest nearby built fabric."""
    names = sorted({name for _, _, name in places})
    if requested:
        matches = [name for name in names if name.casefold() == requested.casefold()]
        if not matches:
            raise ValueError(f"Unknown OSM sector {requested!r}; available sectors: {', '.join(names)}")
        return matches[0]
    density = {name: 0 for name in names}
    for element in elements:
        tags = element.get("tags", {})
        if not (tags.get("building") or tags.get("shop") or tags.get("office")):
            continue
        coord = _coordinate(element)
        if coord is not None:
            density[_nearest_name(coord, places)] += 1
    return max(names, key=lambda name: (density[name], name))


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
    if any(tags.get("shop") or tags.get("office") or tags.get("building") == "commercial" for tags in nearby):
        return "commercial"
    apartments = sum(tags.get("building") in {"apartments", "residential"} for tags in nearby)
    houses = sum(tags.get("building") in {"house", "detached"} for tags in nearby)
    if apartments and houses:
        return "mixed"
    if apartments:
        return "multistorey"
    if houses:
        return "private"
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
    elements = [
        element
        for element in payload.get("elements", [])
        if (coord := _coordinate(element)) is not None and point_in_boundary(coord, boundary)
    ]
    places: list[tuple[float, float, str]] = []
    for element in elements:
        tags = element.get("tags", {})
        coord = _coordinate(element)
        if tags.get("place") in {"suburb", "neighbourhood"} and tags.get("name"):
            places.append((*coord, str(tags["name"])))
    if not places:
        raise ValueError("OSM response contains no real suburb/neighbourhood inside the polygon")
    selected_sector = _select_sector(places, elements, sector)

    def in_sector(element: dict) -> bool:
        coord = _coordinate(element)
        return coord is not None and _nearest_name(coord, places) == selected_sector

    sector_elements = [element for element in elements if in_sector(element)]
    roads = [
        element
        for element in sector_elements
        if element.get("tags", {}).get("highway") in {"residential", "living_street"}
        and element.get("geometry")
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
        raise ValueError(f"OSM sector {selected_sector!r} contains no residential street geometry for top-up")

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
            and _nearest_name(point, places) == selected_sector
            and all(haversine_meters(point, (row[0], row[1])) >= MIN_SITE_SPACING_M for row in chosen)
        ):
            chosen.append((lat, lon, street, False, {}))
    if len(chosen) < n_sites:
        raise ValueError(f"Could place only {len(chosen)} sites at least {MIN_SITE_SPACING_M:.0f} m apart")

    rows = []
    for number, (lat, lon, street, source_real, tags) in enumerate(chosen, start=1):
        point = (lat, lon)
        district = selected_sector
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
                "sector": selected_sector,
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
    return pd.DataFrame(rows)


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/world.csv"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sites", type=int, default=DEFAULT_SITE_COUNT)
    parser.add_argument("--sector", help="Exact OSM suburb/neighbourhood name")
    args = parser.parse_args()
    world = generate_world(seed=args.seed, n_sites=args.sites, sector=args.sector)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    world.to_csv(args.out, index=False)
    real = int(world["source_real"].sum())
    print(
        f"Wrote {len(world)} sites in sector {world.iloc[0]['sector']} to {args.out}: "
        f"{real} real OSM, {len(world) - real} synthesized"
    )


if __name__ == "__main__":
    _main()
