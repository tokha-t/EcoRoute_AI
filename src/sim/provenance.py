"""Human-readable provenance and area-mix labels for simulated worlds."""

from __future__ import annotations

import pandas as pd


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
