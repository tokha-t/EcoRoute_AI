"""Shared tunable constants for EcoRoute AI.

Single home for the cross-module tunables so a value is set in exactly one
place instead of drifting between modules. The original modules re-import these
names, so existing imports (e.g. ``from src.savings import FUEL_COST_KZT_PER_LITER``)
keep working unchanged. This module intentionally has no heavy dependencies — it
must stay importable from anywhere without pulling in ortools, pandas, or PIL.
"""

from __future__ import annotations

# --- Savings model (src/savings.py) --------------------------------------
AVERAGE_TRUCK_SPEED_KMH = 25
FUEL_CONSUMPTION_LITERS_PER_KM = 0.35
CO2_KG_PER_LITER_DIESEL = 2.68
STOP_TIME_MINUTES_PER_BIN = 2
FUEL_COST_KZT_PER_LITER = 295

# --- Load estimate (src/optimize/solver.py) ------------------------------
# Mixed residential waste runs ~0.10-0.15 kg/L uncompacted; 0.12 (=120 kg/m3)
# is the middle of that band. Calibrate per district with weighbridge data (V2).
DENSITY_KG_PER_L = 0.12

# --- Road distances (src/optimize/distances.py) --------------------------
DETOUR_FACTOR = 1.4  # straight-line -> road-distance estimate (spec 6.3)

# --- Photo fill estimator (src/photo_fill/estimator.py) ------------------
VLM_MODEL = "claude-haiku-4-5"  # v0 backend: Anthropic vision API
CONFIDENCE_THRESHOLD = 0.6  # below this, a photo goes to the manual-check list

# --- Prediction / max-interval rule (src/predict.py) ---------------------
# A site overdue this many days is always Critical + must_serve, regardless of
# predicted fill (P0, SPEC_V1 6.4). Never remove or weaken.
MAX_INTERVAL_DAYS = 3

# --- V2 predictive collection simulation --------------------------------
APP_VERSION = "2.2"
# Query envelope of the authoritative multi-part OSM relation. It may narrow
# remote requests but must never be used as the district boundary.
BAIKONUR_BBOX = (51.1475427, 71.2980936, 51.3511101, 71.7063332)
# The operator yard is not confirmed yet. This plausible point in the modeled
# Өндіріс sector is UI-editable and must always be labelled as assumed.
DEPOT_COORDS = (51.190491, 71.428779)
DEPOT_COORDS_ASSUMED = True
# Real OSM landfill way 259330214, selected as the closest mapped landfill to
# the pilot sector and cached in data/cache/osm/landfill.geojson.
LANDFILL_COORDS = (51.203790, 71.506687)

RED_THRESHOLD = 70.0
YELLOW_THRESHOLD = 21.0
OVERFLOW_LIMIT = 100.0
PLANNING_HORIZON_DAYS = 1

LANDFILL_SERVICE_SECONDS = 900.0
MAX_DUMP_TRIPS = 4
# Calibrated by the V2.2 six-point marginal-detour frontier. The policy serves
# a YELLOW site only when its added road distance per m³ is within this budget.
DETOUR_BUDGET_M_PER_M3 = 0.0
FALLBACK_COST_PER_M3_M = 1_200.0
SIMULATION_DAYS = 30

BASE_RATE = {
    "multistorey": 42.0,
    "private": 14.0,
    "commercial": 55.0,
    "mixed": 30.0,
}
WEEKDAY_FACTOR_RESIDENTIAL = (1.0, 1.0, 1.0, 1.0, 1.15, 1.25, 1.1)
WEEKDAY_FACTOR_COMMERCIAL = (1.0, 1.0, 1.0, 1.0, 1.15, 0.6, 0.6)
