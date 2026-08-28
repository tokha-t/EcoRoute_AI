# ♻️ EcoRoute AI

**Predictive waste collection & route optimization for smart cities.**

EcoRoute AI simulates how quickly collection sites fill, classifies mandatory and opportunistic stops, and builds capacity- and shift-safe truck routes with landfill dump trips. The current pilot world covers the OSM sector `Өндіріс` inside Astana's multi-part Baikonur district boundary.

🔗 **Live V2.1 demo:** [ecoroute-ai-baikonur.streamlit.app](https://ecoroute-ai-baikonur.streamlit.app/)  ·  🏙️ **Built for:** Astana Innovations Accelerator — Ecology & Urban Environment

![EcoRoute AI dashboard](assets/screenshots/dashboard.png)

---

## The problem

Cities run waste trucks on **fixed routes** — the same loop every day, emptying bins that are only half full while busy areas overflow. That wastes fuel, time, and municipal budget, and adds avoidable emissions.

## The solution

EcoRoute AI replaces the fixed schedule with a demand-driven one, in four steps:

1. **Project** tomorrow's fill from each site's stable synthetic accumulation rate.
2. **Prioritise** RED stops as mandatory and evaluate YELLOW stops by detour per m³.
3. **Optimise** per-truck routes with OR-Tools, capacity resets at the landfill, and shift limits.
4. **Compare** fixed, fixed-naive, predictive, and RED-only policies over 30 days.

A dispatcher sees the full plan on one map before the shift, tunes how aggressive collection is, and exports the exact route order.

## Results (latest 30-day simulation)

| Metric | Value |
|---|---|
| World | 250 sites in `Өндіріс`, the densest selected Baikonur pilot sector |
| Coordinate provenance | 13 real OSM waste records + 237 sites generated on real streets; all inside the OSM administrative polygon |
| Distance source | Committed OSRM road cache: full 252×252 matrix, 100% of default 30-day route edges covered offline |
| Predictive max-interval violations | 0 |
| Predictive overflow events vs fixed | 171 vs 237 (-27.8%) |
| Automatically selected YELLOW tolerance | 0.0 (largest tested value improving both distance and overflow) |

Policy comparison (same four-truck fleet and accumulation sequence):

| Policy | Distance | Overflow events | Max-interval violations |
|---|---:|---:|---:|
| Fixed | 5,565 km | 237 | 50 |
| Predictive, selected tolerance 0.0 | 4,819 km | 171 | 0 |
| Predictive, tolerance 0.25 | 6,082 km | 127 | 0 |

> Every KPI above is **simulated**. The run demonstrates policy behavior on real OSM geometry; it does not estimate measured Astana savings. The required seven-point sweep selected tolerance 0.0 because it is the largest tested value improving both headline measures: 13.4% less distance and 27.8% fewer overflow events than fixed. The full report keeps the service-quality trade-off visible instead of hiding it behind one savings figure.

## Key features

- Authoritative cached multi-part OSM district boundary, including detached polygons; no bbox fallback
- Deterministic 250-site pilot world in the densest OSM sector, with every site polygon-validated
- Exact RED/YELLOW/GREEN classification with max-interval precedence
- Two-pass OR-Tools routing with explainable, volume-scaled YELLOW penalties
- Repeatable landfill dump visits, mandatory empty return, capacity, and shift enforcement
- Build-time OSRM artifact with exact road distances and compressed street geometry; no routing server required in production
- Real OSM landfill plus an editable, explicitly assumed depot that must be present in the road cache
- Deterministic 31-snapshot trajectory: day 0 → 30 → 0 is lookup-only after the initial cached build
- RU/EN map with one/all-truck filtering, explicit landfill → depot legs, and loud dashed-line fallback warnings
- Russian route sheets with ordered stops, ETA, cumulative load, per-leg distance, manual overrides, and signatures
- Session-persistent dispatcher include/exclude overrides that re-solve only the selected day
- Downloadable 30-day report, daily KPI CSV, and full tolerance frontier table/chart

## Data

Real municipal fill history is not available yet, so accumulation and initial fill are synthetic. The district polygon, sector name, street geometry, mapped waste sites, and addresses where tagged come from OpenStreetMap. Additional site coordinates are synthesized along those real streets, kept at least 60 m apart, and rejected unless they lie inside the authoritative polygon. The generated snapshot records coordinate provenance per site.

## Tech stack

Python · Streamlit · scikit-learn · pandas · numpy · Plotly · joblib

## Run locally

```bash
python -m venv venv
# macOS/Linux:
source venv/bin/activate
# Windows:
venv\Scripts\activate

pip install -r requirements.txt
streamlit run app.py
```

The repository includes both the generated Baikonur world and its 0.52 MB road artifact, so the app routes fully offline. Regenerate the world with `python -m src.sim.world --out data/world.csv`; run the comparison with `python -m src.sim.run --days 30 --seed 42`.

To rebuild road data on a machine where OSRM is already running:

```bash
python scripts/build_road_cache.py --world data/world.csv --osrm http://localhost:5000 --k 25
```

## Project structure

```
ecoroute-ai/
├── app.py               # Streamlit dashboard
├── src/                 # data generation, model, prediction, routing, savings, maps
│   ├── sim/             # V2 world, fill rules, trajectory cache, and 30-day engine
│   ├── reports/         # printable dispatcher route sheets
│   └── geo/             # cached OSM infrastructure + polyline codec
├── data/road_cache/     # full road matrix + compressed route geometry
├── reports/             # 30-day KPIs + tolerance frontier table/chart
├── models/              # trained model + metrics
├── tests/               # core-logic tests
└── assets/screenshots/  # dashboard image(s)
```

## Roadmap

- Real IoT smart-bin sensor integration
- Live traffic-aware routing
- Multi-truck fleet optimisation
- Dynamic scheduling + citizen reporting
- City-manager analytics dashboard

---

*Built for the Astana Innovations Accelerator. Demo city: Astana, Kazakhstan.*
