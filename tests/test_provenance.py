from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.optimize.road_cache import world_hash
from src.sim.provenance import sector_scope_text, sector_scope_warning


def _world() -> pd.DataFrame:
    return pd.DataFrame([{"site_id": "S1", "lat": 51.2, "lon": 71.4}])


def test_sector_scope_warning_matches_only_the_audited_world(tmp_path: Path) -> None:
    world = _world()
    metadata = tmp_path / "world.meta.json"
    metadata.write_text(
        json.dumps(
            {
                "world_hash": world_hash(world),
                "sector_scope": {
                    "validated": False,
                    "sector": "Өндіріс",
                    "sites_inside_polygon": 1,
                    "site_count": 250,
                },
            }
        ),
        encoding="utf-8",
    )

    warning = sector_scope_warning(world, "ru", metadata_path=metadata)
    assert warning is not None
    assert "1 из 250" in warning
    assert "нельзя считать" in warning

    changed = world.assign(lat=[51.3])
    assert sector_scope_warning(changed, metadata_path=metadata) is None


def test_sector_scope_warning_ignores_validated_world(tmp_path: Path) -> None:
    world = _world()
    metadata = tmp_path / "world.meta.json"
    metadata.write_text(
        json.dumps(
            {
                "world_hash": world_hash(world),
                "sector_scope": {
                    "validated": True,
                    "sector": "Жастар",
                    "sites_inside_polygon": 250,
                    "site_count": 250,
                    "containment_pct": 100.0,
                },
            }
        ),
        encoding="utf-8",
    )
    assert sector_scope_warning(world, metadata_path=metadata) is None
    assert sector_scope_text(world, "ru", metadata_path=metadata) == (
        "Границы сектора подтверждены: 250 из 250 площадок (100.0%) внутри полигона Жастар."
    )
