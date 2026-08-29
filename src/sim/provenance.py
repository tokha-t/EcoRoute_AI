"""Human-readable provenance and area-mix labels for simulated worlds."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.optimize.road_cache import world_hash

WORLD_METADATA_PATH = Path(__file__).resolve().parents[2] / "data" / "world.meta.json"


def _operator_mask(world: pd.DataFrame) -> pd.Series:
    """Recognize an eventual operator registry without coupling to one importer."""
    for column in ("source_operator", "operator_data"):
        if column in world:
            return world[column].fillna(False).astype(bool)
    for column in ("source", "source_kind", "data_source"):
        if column in world:
            return world[column].astype(str).str.casefold().isin({"operator", "operator_registry"})
    return pd.Series(False, index=world.index, dtype=bool)


def site_provenance_text(world: pd.DataFrame, lang: str = "ru") -> str:
    """Return the prominent OSM/synthetic (or future operator) disclosure."""
    total = len(world)
    operator_count = int(_operator_mask(world).sum())
    if operator_count:
        return (
            f"Данные оператора: {operator_count} из {total}."
            if lang == "ru"
            else f"Operator data: {operator_count} of {total}."
        )
    real_count = int(world.get("source_real", pd.Series(False, index=world.index)).fillna(False).sum())
    if lang == "ru":
        return f"Реальных площадок из OSM: {real_count} из {total}; остальные размещены на реальных улицах."
    return f"Real OSM sites: {real_count} of {total}; the remainder are placed on real streets."


def area_type_mix_text(world: pd.DataFrame) -> str:
    """Return a stable, descending area-type composition string."""
    if world.empty or "area_type" not in world:
        return "unknown"
    counts = world["area_type"].astype(str).value_counts()
    total = int(counts.sum())
    return ", ".join(
        f"{area_type}: {count} ({count / total * 100:.1f}%)" for area_type, count in counts.items()
    )


def sector_scope_warning(
    world: pd.DataFrame,
    lang: str = "ru",
    *,
    metadata_path: Path = WORLD_METADATA_PATH,
) -> str | None:
    """Describe a failed spatial-sector audit for the exact committed world, if present."""
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError):
        return None
    if metadata.get("world_hash") != world_hash(world):
        return None
    audit = metadata.get("sector_scope", {})
    if not isinstance(audit, dict) or audit.get("validated") is not False:
        return None
    sector = str(audit.get("sector", "unknown"))
    inside = int(audit.get("sites_inside_polygon", 0))
    total = int(audit.get("site_count", len(world)))
    if lang == "ru":
        return (
            f"Проверка границ сектора не пройдена: только {inside} из {total} площадок находится "
            f"внутри OSM-полигона {sector}. Этот замороженный набор нельзя считать подтверждённой "
            "выборкой жилого сектора."
        )
    return (
        f"Sector-boundary validation failed: only {inside} of {total} sites fall inside the "
        f"{sector} OSM polygon. This frozen world must not be presented as a validated residential "
        "sector sample."
    )
