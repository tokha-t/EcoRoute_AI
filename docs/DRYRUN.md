# Demo Dry-Run Checklist — Final QA (SPEC_V1 §9)

Pass criteria: **3 consecutive clean runs**, each **< 3 minutes**, **zero crashes**.
A run is clean when every step below lands inside its time budget with no error
states except the ones the script deliberately shows (fallback badge, low-confidence row).

Click path and talk track: [docs/demo_script.md](demo_script.md).

## Before the first run (not on the clock)

- [ ] `source .venv/bin/activate` (or use `.venv/bin/python` directly)
- [ ] `python -m pytest tests/ -q` → all green
- [ ] `export ANTHROPIC_API_KEY=...` in the shell that will launch the app
      (skip to rehearse the offline fallback — live mode then shows the
      "API key required" notice instead of the uploader, by design)
- [ ] Optional: OSRM Docker up (`docs/osrm-setup.md`); if down, the map badge
      says "straight-line est." — acknowledge it, don't hide it
- [ ] 2–3 container photos in a folder, named after sites (`BIN-0042.jpg`)
- [ ] `streamlit run app.py`, let the first page fully load once
      (first ever load also generates data + trains the demo model)
- [ ] Sidebar reset: Mode = Simulation, threshold 75, CVRP engine,
      2 trucks, 5,000 kg, 8 h

## Timed checklist (per run)

Budgets assume the pre-loaded app; the CVRP solver alone uses a fixed 5 s
search budget, so any interaction that re-plans costs ~5–8 s wall time.

| # | Step | Budget | Run 1 | Run 2 | Run 3 |
|---|------|--------|-------|-------|-------|
| 1 | Framing over loaded dashboard; point at "Simulated data" banner | 0:20 | | | |
| 2 | KPI row + map walkthrough (fallback badge if OSRM down) | 0:25 | | | |
| 3 | Threshold 75 → 85 → 75; plan/KPIs recompute (~6–8 s each) | 0:20 | | | |
| 4 | Engine comparison + max-interval rule sentence | 0:15 | | | |
| 5 | Mode → Live photo; upload 2–3 photos (< 10 s per estimate) | 0:35 | | | |
| 6 | Review table: resolve ⚠️ row, assign sites | 0:20 | | | |
| 7 | Add to today's plan → CVRP recomputes → map + route order | 0:15 | | | |
| 8 | Evaluation tab (confusion matrix or its honest empty state) + close | 0:30 | | | |
|   | **Total** | **3:00** | | | |

Record a run as failed on any crash, traceback, or blank screen — then fix,
and restart the count of consecutive clean runs at zero.

## What final QA verified (2026-07-04)

- **Tests:** 93 pass locally (Python 3.13); CI runs the same suite on 3.11 + 3.13
  (`.github/workflows/ci.yml`, push + PR) with `ruff check` as a lint gate.
- **Offline boot:** with OSRM down, no `ANTHROPIC_API_KEY`, and an empty matrix
  cache, the app fully renders simulation mode (all KPIs, maps, charts), shows
  the "straight-line est." badge, and logs no server or console errors.
- **Offline live mode:** without an API key, live photo mode shows an
  "API key required" notice instead of the uploader; "Today's plan" and the
  evaluation tab stay functional.
- **Determinism:** full pipeline (data gen → training → prediction → 2-opt →
  CVRP) run 5× in separate processes produced byte-identical numbers; the
  synthetic generator and demo model are seeded (seed 42 / random_state 42).
  Locked in by `tests/test_determinism.py`. One caveat: the OR-Tools search
  runs under a 5 s wall-clock limit, so extreme CPU starvation could in theory
  cut the search short — close other heavy apps on the demo laptop.
- **Timing baseline (M2 MacBook-class laptop):** full scripted pipeline runs in
  ~7 s including the 5 s solver budget; cold first app load (data gen + model
  training + first solve) is ~15–20 s — do it before the demo starts.

## Found broken during QA → minimal fixes

1. **No up-front "API key required" state** (offline requirement, this
   milestone): live photo mode only surfaced the missing key as per-photo
   errors after an upload attempt. Fixed: `api_key_available()` in
   `src/photo_fill/estimator.py` + an early notice in `app.py`;
   failure playbook in `docs/demo_script.md` updated to match.
2. **`SPEC_V1_PRE_DATA_MVP.md`** at the repo root was a byte-identical
   duplicate of `docs/SPEC_V1.md` — removed (docs/SPEC_V1.md stays the
   source of truth).
3. **Import ordering** in 10 files — auto-fixed by `ruff check --fix`
   (ruff added as a dev dependency; config in `ruff.toml`). No TODOs, dead
   code, or unused imports were found beyond that.
