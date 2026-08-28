from __future__ import annotations

import pandas as pd

from src.sim.world import generate_world


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
