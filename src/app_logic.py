"""Pure app-level logic behind app.py (SPEC_V1 6.5).

app.py stays UI-only, so everything testable without Streamlit lives here:
mode flags, honesty labeling, and the live-photo -> plan merge pipeline
(estimator output -> editable table rows -> confirmed observations ->
plan-ready sites for src.optimize.solver.plan_routes).
"""

from __future__ import annotations

import pandas as pd

from src.photo_fill.estimator import FILL_CLASSES, PCT_RANGES, UNCERTAIN

MODE_SIMULATION = "Simulation"
MODE_LIVE_PHOTO = "Live photo"
MODES = (MODE_SIMULATION, MODE_LIVE_PHOTO)

# Honesty invariant (CLAUDE.md): synthetic-derived numbers are always labeled.
SIMULATED_BANNER_TEXT = "Simulated data — for demonstration"
SAVINGS_TARGET_TEXT = (
    "Target range: 15–25% distance savings (industry real-world deployments). "
    "The figures above are simulated demo output, not measured results."
)

FLAG_AUTO = "✓ auto"
FLAG_MANUAL_CHECK = "⚠️ manual check"

OBSERVATION_COLUMNS = ("photo", "site_id", "cls", "confidence", "flag", "include")

# One planning number per class: the midpoint of its pct_range.
FILL_PCT_BY_CLASS: dict[str, float] = {
    cls: (low + high) / 2.0 for cls, (low, high) in PCT_RANGES.items()
}

# A photo-confirmed site added by the dispatcher is a deliberate pin, so even
# low classes keep an actionable priority instead of "Skip".
PRIORITY_BY_CLASS: dict[str, str] = {
    "overflowing": "Critical",
    "full": "High",
    "half": "Medium",
    "empty": "Medium",
    UNCERTAIN: "Medium",
}


def show_simulated_banner(mode: str) -> bool:
    """True when savings/metric elements must carry the simulated banner.

    Fails honest: any mode other than live photo is treated as simulation.
    """
    return mode != MODE_LIVE_PHOTO


def include_by_default(cls: str) -> bool:
    """Uncertain photos never enter the plan silently (SPEC_V1 6.2)."""
    return cls in FILL_CLASSES


def guess_site_id(filename: str, known_site_ids: list[str]) -> str:
    """Pre-fill the site picker when the photo filename contains a site id."""
    lowered = filename.lower()
    for site_id in known_site_ids:
        if site_id.lower() in lowered:
            return site_id
    return ""


def observation_rows(estimates: list[dict], known_site_ids: list[str]) -> pd.DataFrame:
    """Editable-table rows from estimator outputs.

    estimates: [{"photo": str, "cls": str, "confidence": float}, ...]
    """
    rows = []
    for estimate in estimates:
        cls = str(estimate["cls"])
        rows.append(
            {
                "photo": str(estimate["photo"]),
                "site_id": guess_site_id(str(estimate["photo"]), known_site_ids),
                "cls": cls,
                "confidence": round(float(estimate["confidence"]), 2),
                "flag": FLAG_MANUAL_CHECK if cls == UNCERTAIN else FLAG_AUTO,
                "include": include_by_default(cls),
            }
        )
    return pd.DataFrame(rows, columns=list(OBSERVATION_COLUMNS))


def confirmed_observations(edited: pd.DataFrame) -> pd.DataFrame:
    """Rows that may enter the plan: included, site chosen, class resolved.

    Uncertain rows are kept out even when ticked — the dispatcher resolves
    them by setting a class after a manual check, never by silent guessing.
    """
    if edited.empty:
        return edited.copy()
    site_ids = edited["site_id"].fillna("").astype(str).str.strip()
    mask = (
        edited["include"].fillna(False).astype(bool)
        & (site_ids != "")
        & edited["cls"].isin(FILL_CLASSES)
    )
    return edited[mask].copy()


def merge_observations(existing: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    """Merge confirmed observations into the day's plan: one row per site.

    When a site appears more than once, the fuller class wins (conservative:
    never under-plan a pickup); on equal fill the newer observation wins.
    """
    frames = [frame for frame in (existing, new) if frame is not None and not frame.empty]
    if not frames:
        return pd.DataFrame(columns=list(OBSERVATION_COLUMNS))
    combined = pd.concat(frames, ignore_index=True)
    combined["_fill"] = combined["cls"].map(FILL_PCT_BY_CLASS)
    merged = (
        combined.sort_values("_fill", kind="stable")
        .drop_duplicates("site_id", keep="last")
        .drop(columns="_fill")
        .sort_values("site_id", kind="stable")
        .reset_index(drop=True)
    )
    return merged


def observations_to_plan_sites(bins_df: pd.DataFrame, observations: pd.DataFrame) -> pd.DataFrame:
    """Join photo observations onto the site registry for plan_routes().

    Returns registry rows plus fill_pct / predicted_fill_pct (class midpoint),
    priority, must_serve=True, and the photo provenance columns. Raises
    ValueError on site ids missing from the registry.
    """
    observations = merge_observations(None, observations)
    if observations.empty:
        return bins_df.iloc[0:0].copy()

    registry = bins_df.set_index("bin_id")
    unknown = sorted(set(observations["site_id"]) - set(registry.index))
    if unknown:
        raise ValueError(f"site ids not in the registry: {unknown}")

    sites = registry.loc[observations["site_id"]].reset_index()
    fill = observations["cls"].map(FILL_PCT_BY_CLASS).astype(float).to_numpy()
    sites["fill_pct"] = fill
    sites["predicted_fill_pct"] = fill
    sites["priority"] = observations["cls"].map(PRIORITY_BY_CLASS).to_numpy()
    sites["must_serve"] = True
    sites["fill_source"] = "photo"
    sites["photo_class"] = observations["cls"].to_numpy()
    sites["photo_confidence"] = observations["confidence"].astype(float).to_numpy()
    sites["bin_id"] = sites["bin_id"].astype(str)
    return sites
