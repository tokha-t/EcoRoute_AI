# ♻️ EcoRoute AI

**Predictive waste collection & route optimization for smart cities.**

EcoRoute AI simulates how quickly collection sites fill, classifies mandatory and opportunistic stops, and builds capacity- and shift-safe truck routes with landfill dump trips. Built on real Baikonur-district coordinates in Astana.

🔗 **Live V2 demo:** [ecoroute-ai-baikonur.streamlit.app](https://ecoroute-ai-baikonur.streamlit.app/)  ·  🏙️ **Built for:** Astana Innovations Accelerator — Ecology & Urban Environment

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
| World | 250 sites in Baikonur district |
| Coordinate provenance | 74 real OSM waste records + 176 sites snapped to real streets |
| Predictive max-interval violations | 0 |
| Predictive overflow events vs fixed | 199 vs 298 (-33.2%) |

Policy comparison (same four-truck fleet and accumulation sequence):

| Policy | Distance | Overflow events | Max-interval violations |
|---|---:|---:|---:|
| Fixed | 5,273 km | 298 | 24 |
| Predictive RED + YELLOW | 5,806 km | 199 | 0 |
| Predictive RED only (analysis) | 3,964 km | 242 | 0 |

> Every KPI above is **simulated**. The run demonstrates policy behavior on real coordinates; it does not estimate measured Astana savings. The default RED+YELLOW policy trades 10.1% more distance than fixed for 33.2% fewer overflow events in this generated world, while RED-only is an analysis mode—not the operating default.

## Key features

- Deterministic 250-site world generated on cached OSM streets and real district names
- Exact RED/YELLOW/GREEN classification with max-interval precedence
- Two-pass OR-Tools routing with explainable, volume-scaled YELLOW penalties
- Repeatable landfill dump visits with capacity and shift enforcement
- RU/EN interactive map and per-truck route panels with an OSRM-offline fallback
- Downloadable 30-day Markdown report and daily KPI CSV

## Data

Real municipal fill history is not available yet, so accumulation and initial fill are synthetic. Coordinates, street geometry, addresses where tagged, and district names come from OpenStreetMap. The generated snapshot records coordinate provenance per site.

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

The repository includes the generated Baikonur world for offline deployment. Regenerate it with `python -m src.sim.world --out data/world.csv`; run the comparison with `python -m src.sim.run --days 30 --seed 42`.

## Project structure

```
ecoroute-ai/
├── app.py               # Streamlit dashboard
├── src/                 # data generation, model, prediction, routing, savings, maps
│   ├── sim/             # V2 world, fill rules, and 30-day engine
│   └── geo/             # cached Overpass client
├── data/                # generated demo data (csv)
├── reports/             # generated 30-day Markdown + daily KPI CSV
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
