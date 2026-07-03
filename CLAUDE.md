# EcoRoute AI

Predictive waste collection & route optimization for cities. Streamlit MVP.
Current phase: V1 "pre-data MVP" — see docs/SPEC_V1.md (source of truth).
Demo city: Astana. Owner: solo founder; keep everything simple enough for one person to maintain.

## Structure
- app.py — Streamlit dashboard (UI only; no business logic here)
- src/data_generator.py — synthetic demo data (SIMULATION ONLY, will be replaced by real feeds)
- src/predict.py — fill prediction + priority (max-interval rule lives here)
- src/routing.py — legacy NN+2-opt (being replaced by src/optimize/)
- src/optimize/ — OR-Tools CVRP + OSRM distances (V1, new)
- src/photo_fill/ — photo → fill-level estimation (V1, new)
- data/, models/ — generated artifacts, never hand-edit
- tests/ — pytest; must stay green

## Commands
- Run app: streamlit run app.py
- Tests: python -m pytest tests/ -q   (ALWAYS run before declaring any task done)
- Retrain demo model: python -m src.train_model

## Rules
- Honesty invariant: any number derived from synthetic data must be labeled "simulated" in the UI. Never present simulation output as measured.
- The max-interval rule overrides model predictions: site overdue ≥ MAX_INTERVAL_DAYS is always Critical + must-serve. Never remove or weaken this.
- No new heavy dependencies without asking (allowed: ortools, requests/httpx, pillow, pytest).
- No databases, no auth, no FastAPI in V1 — Streamlit monolith stays.
- Every new module gets unit tests in the same PR. Failing tests = task not done.
- Don't touch data/ or models/ artifacts by hand; regenerate via code.
- Style: type hints, small pure functions, module-level constants for tunables.
- Language: code/comments in English; UI strings may be English (Russian localization is V2).
