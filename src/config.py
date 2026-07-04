"""Shared tunable constants for EcoRoute AI (SPEC_V1).

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
