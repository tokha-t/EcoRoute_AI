from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.geo.overpass import point_in_ring
from src.sim.world import _area_type, _select_sector, generate_world


def mock_boundary() -> dict:
    return {
        "type": "Feature",
        "properties": {"name": "Test district"},
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [
                [
                    [
                        [71.30, 51.10],
                        [71.50, 51.10],
                        [71.50, 51.25],
                        [71.30, 51.25],
                        [71.30, 51.10],
                    ]
                ]
            ],
        },
    }


def mock_osm_payload() -> dict:
    elements: list[dict] = [
        {
            "type": "node",
            "id": 1,
            "lat": 51.175,
            "lon": 71.405,
            "tags": {"place": "neighbourhood", "name": "Жастар"},
        },
        {
            "type": "node",
            "id": 2,
            "lat": 51.185,
            "lon": 71.42,
            "tags": {"building": "apartments"},
        },
        {
            "type": "node",
            "id": 3,
            "lat": 51.176,
            "lon": 71.406,
            "tags": {"amenity": "waste_disposal", "addr:street": "Абай", "addr:housenumber": "1"},
        },
        {
            "type": "way",
            "id": 4,
            "tags": {"landuse": "residential"},
            "geometry": [
                {"lat": 51.11, "lon": 71.31},
                {"lat": 51.11, "lon": 71.445},
                {"lat": 51.24, "lon": 71.445},
                {"lat": 51.24, "lon": 71.31},
                {"lat": 51.11, "lon": 71.31},
            ],
        },
    ]
    for index in range(30):
        lat = 51.135 + index * 0.0027
        elements.append(
            {
                "type": "way",
                "id": 100 + index,
                "tags": {"highway": "residential", "name": f"OSM-{index}"},
                "geometry": [{"lat": lat, "lon": 71.345 + step * 0.006} for step in range(21)],
            }
        )
    return {"elements": elements}


def test_world_contract_and_determinism() -> None:
    first = generate_world(
        seed=7,
        n_sites=210,
        payload=mock_osm_payload(),
        boundary=mock_boundary(),
    )
    second = generate_world(
        seed=7,
        n_sites=210,
        payload=mock_osm_payload(),
        boundary=mock_boundary(),
    )

    pd.testing.assert_frame_equal(first, second)
    assert len(first) >= 200
    assert set(first["district"]) == {"Жастар"}
    assert set(first["sector"]) == {"Жастар"}
    assert first["source_real"].sum() == 1
    assert (first["capacity_liters"] == first["containers"] * first["container_liters"]).all()
    assert first["daily_fill_rate_pct"].between(3, 95).all()
    assert first["lat"].between(51.13, 51.22).all()
    assert first["lon"].between(71.34, 71.47).all()
    ring = first.attrs["sector_feature"]["geometry"]["coordinates"][0]
    containment = sum(
        point_in_ring((float(row.lat), float(row.lon)), ring)
        for row in first.itertuples(index=False)
    ) / len(first)
    assert containment >= 0.95
    assert first.attrs["sector_scope"]["validated"] is True


def test_default_sector_uses_residential_building_count() -> None:
    payload = mock_osm_payload()
    payload["elements"].extend(
        [
            {
                "type": "node",
                "id": 900,
                "lat": 51.21,
                "lon": 71.47,
                "tags": {"place": "neighbourhood", "name": "Residential"},
            },
            *[
                {
                    "type": "node",
                    "id": 910 + index,
                    "lat": 51.209 + index * 0.0001,
                    "lon": 71.469,
                    "tags": {"building": "house"},
                }
                for index in range(3)
            ],
            *[
                {
                    "type": "node",
                    "id": 920 + index,
                    "lat": 51.18 + index * 0.0001,
                    "lon": 71.42,
                    "tags": {"building": "commercial"},
                }
                for index in range(20)
            ],
            {
                "type": "way",
                "id": 950,
                "tags": {"landuse": "residential"},
                "geometry": [
                    {"lat": 51.20, "lon": 71.455},
                    {"lat": 51.20, "lon": 71.49},
                    {"lat": 51.24, "lon": 71.49},
                    {"lat": 51.24, "lon": 71.455},
                    {"lat": 51.20, "lon": 71.455},
                ],
            },
        ]
    )
    selected = _select_sector(
        [(51.175, 71.405, "Жастар"), (51.21, 71.47, "Residential")],
        payload["elements"],
        None,
    )
    assert selected == "Residential"


def test_area_type_uses_dominant_surrounding_evidence() -> None:
    point = (51.18, 71.42)
    residential_context = [
        (51.18, 71.42, {"building": "apartments"}),
        (51.1801, 71.42, {"building": "apartments"}),
        (51.18, 71.4201, {"shop": "convenience"}),
    ]
    ambiguous_commercial_context = [
        (51.18, 71.42, {"building": "apartments"}),
        (51.1801, 71.42, {"shop": "convenience"}),
        (51.18, 71.4201, {"office": "company"}),
    ]
    commercial_context = [
        (51.18, 71.42, {"building": "apartments"}),
        (51.1801, 71.42, {"building": "retail", "shop": "supermarket"}),
        (51.18, 71.4201, {"building": "commercial", "shop": "clothes"}),
        (51.1801, 71.4201, {"building": "office", "office": "company"}),
    ]
    assert _area_type(point, residential_context) == "multistorey"
    assert _area_type(point, ambiguous_commercial_context) == "multistorey"
    assert _area_type(point, commercial_context) == "commercial"


def test_ground_floor_shop_does_not_turn_apartment_block_commercial() -> None:
    point = (51.18, 71.42)
    context = [
        (51.18, 71.42, {"building": "apartments"}),
        (51.1801, 71.42, {"shop": "convenience"}),
        (51.1802, 71.42, {"building": "yes", "addr:street": "Тараз көшесі"}),
    ]
    result = _area_type(point, context, {"тараз көшесі"})
    assert result in {"multistorey", "mixed"}
    assert result != "commercial"


def test_committed_world_is_inside_named_residential_sector_polygon() -> None:
    project_root = Path(__file__).resolve().parents[1]
    world = pd.read_csv(project_root / "data" / "world.csv")
    feature = json.loads(
        (project_root / "data" / "cache" / "osm" / "selected_sector.geojson").read_text(
            encoding="utf-8"
        )
    )
    ring = feature["geometry"]["coordinates"][0]
    inside = sum(
        point_in_ring((float(row.lat), float(row.lon)), ring)
        for row in world.itertuples(index=False)
    )
    assert inside / len(world) >= 0.95
    assert set(world["sector"]) == {feature["properties"]["name"]} == {"Жастар"}
    assert world["area_type"].isin({"mixed", "private", "multistorey"}).mean() > 0.5
    assert world["area_type"].eq("commercial").mean() < 0.4
