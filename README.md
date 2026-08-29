# ♻️ EcoRoute AI

**Predictive waste collection & route optimization for smart cities.**

EcoRoute AI simulates how quickly collection sites fill, classifies mandatory and opportunistic stops, and builds capacity- and shift-safe truck routes with landfill dump trips. The frozen 250-site demo world lies inside Astana's multi-part Baikonur district boundary; its historical `Өндіріс` label came from nearest-place assignment and is not a polygon-validated sector claim.

🔗 **Live V2.2 demo:** [ecoroute-ai-baikonur.streamlit.app](https://ecoroute-ai-baikonur.streamlit.app/)  ·  🏙️ **Built for:** Astana Innovations Accelerator — Ecology & Urban Environment

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
| World | 250 sites inside the Baikonur district; historically labelled `Өндіріс` by nearest-place assignment |
| Sector-scope audit | **Not passed:** 1 of 250 cached sites lies inside the actual OSM `Өндіріс` polygon; residential-sector acceptance remains pending |
| Area-type mix | commercial 48.0%, mixed 43.2%, private 8.0%, multistorey 0.8% |
| Coordinate provenance | **13 of 250 real OSM waste records; the other 237 are placed on real streets** |
| Distance source | Committed OSRM road cache: full 252×252 matrix, 100% of default 30-day route edges covered offline |
| Predictive max-interval violations | 0 |
| Fixed max-interval violations | 0 — the baseline is now a compliant, idealised calendar |
| Predictive overflow events vs fixed | 171 vs 195 (-12.3%) |
| Automatically selected YELLOW detour budget | 0 m/m³; it is the only tested point improving both distance and overflow, so the report explicitly flags the yellow rule as inert |

Policy comparison (same four-truck fleet and accumulation sequence):

| Policy | Distance | Overflow events | Max-interval violations |
|---|---:|---:|---:|
| Fixed | 6,080 km | 195 | 0 |
| Predictive, selected detour budget 0 m/m³ | 5,095 km | 171 | 0 |
| Predictive, detour budget 100 m/m³ | 7,412 km | 121 | 0 |

> Every KPI above is **simulated**. The run demonstrates policy behavior on the frozen district-wide world and must not be presented as a validated residential-sector result. It does not estimate measured Astana savings. V2.2 first tested the denser 0–0.25 tolerance range, confirmed that it was saturated, then switched to the interpretable `{0, 100, 200, 400, 800, 1600}` m/m³ detour frontier required by the specification. The selected point drives 16.2% less distance and records 12.3% fewer overflow events than the charitable fixed baseline. Because that point serves no opportunistic YELLOW sites, the report says so plainly rather than claiming that rule contributes to the result.

## Key features

- Authoritative cached multi-part OSM district boundary, including detached polygons; no bbox fallback
- Deterministic 250-site world with every site district-polygon-validated; future generation ranks sectors by residential-building count and supports `--sector`
- Exact RED/YELLOW/GREEN classification with max-interval precedence
- Two-pass OR-Tools routing with an explainable marginal YELLOW detour budget in metres per m³
- Repeatable landfill dump visits, mandatory empty return, capacity, and shift enforcement
- Build-time OSRM artifact with exact road distances and compressed street geometry; no routing server required in production
- Real OSM landfill plus an editable, explicitly assumed depot that must be present in the road cache
- Deterministic 31-snapshot trajectory: day 0 → 30 → 0 is lookup-only after the initial cached build
- RU/EN map with one/all-truck filtering, explicit landfill → depot legs, and loud dashed-line fallback warnings
- Russian route sheets with ordered stops, ETA, cumulative load, per-leg distance, manual overrides, signatures, and prominent data provenance
- Session-persistent dispatcher include/exclude overrides that re-solve only the selected day
- Downloadable 30-day report, daily KPI CSV, and full detour-budget frontier table/chart

## Data

Real municipal fill history is not available yet, so accumulation and initial fill are synthetic. The district polygon, street geometry, mapped waste sites, and addresses where tagged come from OpenStreetMap. Additional site coordinates are synthesized along those real streets, kept at least 60 m apart, and rejected unless they lie inside the authoritative district polygon. The committed world records coordinate provenance per site; `data/world.meta.json` separately records that its historical sector label failed polygon-containment audit.

The [Baikonur district administration](https://www.gov.kz/memleket/entities/astana-baikonyr?lang=en) lists Өндіріс as a residential massif. That does not validate this particular sample: only one committed site lies inside the current [OSM Өндіріс polygon](https://www.openstreetmap.org/way/1276713306). A compliant 150–300-site residential-sector world therefore needs new coordinates and a matching road-cache rebuild; the V2.2 instruction explicitly forbids changing that cache in this revision.

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
├── reports/             # 30-day KPIs + detour-budget frontier table/chart
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
