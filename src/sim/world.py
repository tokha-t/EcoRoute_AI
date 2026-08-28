"""Deterministic synthetic Baikonur collection world on real OSM geometry."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from src.config import (
    BAIKONUR_BBOX,
    BASE_RATE,
    WEEKDAY_FACTOR_COMMERCIAL,
    WEEKDAY_FACTOR_RESIDENTIAL,
)
from src.geo.overpass import query_overpass
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
    factors = (
        WEEKDAY_FACTOR_COMMERCIAL
        if area_type == "commercial"
        else WEEKDAY_FACTOR_RESIDENTIAL
    )
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


def _road_candidates(
    roads: list[dict], rng: np.random.Generator
) -> Iterable[tuple[float, float, str]]:
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


def _area_type(
    point: tuple[float, float], features: list[tuple[float, float, dict]]
) -> str:
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
    bbox: tuple[float, float, float, float] = BAIKONUR_BBOX,
    *,
    payload: dict | None = None,
) -> pd.DataFrame:
    """Generate the §2.1 site table; identical seed + OSM payload is identical."""
    if n_sites < 1:
        raise ValueError("n_sites must be positive")
    payload = payload or query_overpass(build_world_query(bbox))
    elements = payload.get("elements", [])
    places: list[tuple[float, float, str]] = []
    roads: list[dict] = []
    waste: list[dict] = []
    features: list[tuple[float, float, dict]] = []
    for element in elements:
        tags = element.get("tags", {})
        coord = _coordinate(element)
        if coord is None:
            continue
        if tags.get("place") in {"suburb", "neighbourhood"} and tags.get("name"):
            places.append((*coord, str(tags["name"])))
        if tags.get("highway") in {"residential", "living_street"} and element.get("geometry"):
            roads.append(element)
        if tags.get("amenity") in {"waste_disposal", "recycling"}:
            waste.append(element)
        if tags.get("building") or tags.get("shop") or tags.get("office"):
            features.append((*coord, tags))
    if not places:
        raise ValueError("OSM response contains no real suburb/neighbourhood names")
    if not roads and len(waste) < n_sites:
        raise ValueError("OSM response contains no residential street geometry for site top-up")

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
        if coord and all(haversine_meters(coord, (row[0], row[1])) >= MIN_SITE_SPACING_M for row in chosen):
            tags = element.get("tags", {})
            chosen.append((*coord, str(tags.get("addr:street", "")), True, tags))
            if len(chosen) == n_sites:
                break

    candidates = _road_candidates(roads, rng)
    attempts = 0
    while len(chosen) < n_sites and attempts < n_sites * 1000:
        attempts += 1
        try:
            lat, lon, street = next(candidates)
        except StopIteration as exc:
            raise ValueError("Not enough OSM street geometry to generate the requested world") from exc
        point = (lat, lon)
        if all(haversine_meters(point, (row[0], row[1])) >= MIN_SITE_SPACING_M for row in chosen):
            chosen.append((lat, lon, street, False, {}))
    if len(chosen) < n_sites:
        raise ValueError(f"Could place only {len(chosen)} sites at least {MIN_SITE_SPACING_M:.0f} m apart")

    rows = []
    for number, (lat, lon, street, source_real, tags) in enumerate(chosen, start=1):
        point = (lat, lon)
        district = _nearest_name(point, places)
        if tags.get("addr:street"):
            street = str(tags["addr:street"])
        if not street and named_roads:
            street = _nearest_name(point, named_roads)
        house = tags.get("addr:housenumber")
        address = (
            f"ул. {street}{', ' + str(house) if house else ''}, район {district}"
            if street
            else district
        )
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
    args = parser.parse_args()
    world = generate_world(seed=args.seed, n_sites=args.sites)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    world.to_csv(args.out, index=False)
    real = int(world["source_real"].sum())
    print(f"Wrote {len(world)} sites to {args.out}: {real} real OSM, {len(world) - real} synthesized")


if __name__ == "__main__":
    _main()
