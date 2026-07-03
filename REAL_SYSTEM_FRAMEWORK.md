# EcoRoute AI — from demo to real system

Honest engineering framework. Written 2026-07-02, after code audit + market verification.

---

## 1. What the current demo actually is (no illusions)

`data_generator.py` creates features AND the target from a known formula:
`target = 0.58 × previous_fill + f(features) + noise(σ=4.5)`.
The RandomForest then approximates its own data generator. R² = 0.929 measures noise level, not predictive power. So yes — today this is a **UI + plumbing demo**: real algorithms (NN + 2-opt), real dashboard, zero real signal.

That's fine for a hackathon. The plan below is what makes the AI real.

Also honest: the 87.4% distance reduction compares *different service levels* (visit 29 bins vs all 180). A published meta-analysis of IoT-enabled waste routing finds **~21.5% average distance reduction, and only ~12.4% in real-world deployments** (simulations: ~40%). Sensoneo's best municipal cases: 43–63% cost savings where bins were being collected at 24–45% full. **Pitch 15–30% as the realistic target. Never 87%.**

## 2. What data actually exists in Astana (verified July 2026)

This changes everything — Астана Тазалық already runs:

| Asset | Status | What it gives you |
|---|---|---|
| GPS on every truck (since ~2024, dispatch control system) | LIVE | Trip traces → which sites served, when, route km |
| ИС «Waste Management» — digital map of container sites | LIVE | Site registry: locations, container counts/types |
| **Driver photo "before/after" at each site, anti-fraud timestamped** | LIVE | **Per-visit fill-level ground truth via computer vision** |
| Weighbridge at landfill per truck trip | Standard practice | Mass per trip → per-site generation rates |
| iKomek 109 complaints | LIVE | Overflow event labels |

The photo workflow is the discovery. Everyone assumes you need IoT sensors for ground truth. Astana drivers **already photograph every container site at every visit**. A vision model that scores each "before" photo (empty / ~50% / full / overflowing) turns the operator's existing compliance process into a training-label pipeline. No hardware, no procurement, labels from day one, growing daily.

This is your moat and your accelerator story: *"The city already collects the data; nobody turns it into decisions."*

## 3. The real ML formulation

### Unit of prediction
Not a bin — a **container site** (контейнерная площадка, 2–8 bins sharing one stop). Fewer units, more signal per unit, matches how trucks actually stop and how the city's registry is structured.

### State model (the core)
For site *s* at time *t*:

```
V̂(s,t) = V_after_last_pickup(s) + ∫ rate(s,τ) dτ   from last pickup to t
```

- `rate(s,τ)` = waste arrival rate: function of site features (housing type, population nearby, commerce), day-of-week, season, weather, holidays.
- Decision output is NOT "fill %" — it's **P(V(s,t_next) ≥ capacity)**: probability of overflow before the *next* possible visit. Serve site today iff that risk > ε. Use quantile predictions (P80/P90), not means — overflows cost politically, empty runs cost only fuel.

### Ground-truth channels (ranked by practicality)
1. **Driver photos + CV** — per-visit fill class. Bootstrap: score 2–5k photos with a VLM (zero-shot "how full is this container, 0–100%?"), hand-verify a sample, fine-tune a small classifier (or keep VLM if unit cost is fine). Handles snow/night via class granularity, not precision.
2. **Weighbridge disaggregation** — trip mass = Σ site masses; with many overlapping trips, solve per-site average rates via constrained least squares (NNLS). Noisy (density varies ~2×), gives kg/day trends, validates channel 1.
3. **Complaints** — sparse but free overflow labels (V ≥ capacity events).
4. *(Optional, later)* 30–50 sensors on a stratified sample purely as calibration instruments. Not needed to start, thanks to photos.

### Model ladder (build in this order)
- **v0 — per-site empirical rate** (1 week): rate = mean observed Δfill/day from photo labels, shrunk toward district×housing-type mean (empirical Bayes — solves cold start for new sites). Accumulate linearly. *This will already capture most of the value.*
- **v1 — gradient boosting** (LightGBM): predict Δfill/day from site features + calendar + weather + recent history. Quantile objective (P50/P90). Needs ~2–3 months of accumulated photo labels to beat v0. Evaluate with rolling-origin backtests only.
- **v2 — survival framing** (optional, if v1 plateaus): time-to-overflow with censoring (site emptied before full = censored observation). Elegant, handles irregular visits natively.

**Do not skip v0.** If v1 beats v0 by <10% on backtest, ship v0 — simpler to explain to the Akimat and to debug at -30°C.

### Evaluation that matters (kill R²)
- Overflow rate per 1,000 site-days (must be ≤ current ops)
- Mean fill-at-pickup (target 70–85%; Sensoneo found cities collecting at 24–45%)
- km and truck-hours per tonne collected
- Missed-pickup / max-interval violations (must be 0 — implement the hard rule you already pitch)

## 4. Routing: from TSP toy to real dispatch

Current: single truck, no capacity, straight-line distances. Real problem:

1. **Distances**: OSRM (self-hosted, free) with Kazakhstan OSM extract → real road travel-time matrix. ~2 days of work.
2. **Solver**: OR-Tools CVRP. Constraints: truck capacity (kg AND m³ — compaction ratio per truck type), shift duration, mandatory sites (max-interval rule, critical predictions), multiple trucks.
3. **Landfill trips**: trucks dump mid-shift when full → VRP with intermediate facilities. OR-Tools handles via route refills; this is the fiddliest part of the routing work.
4. Winter reality: snow closures, yard access — keep a dispatcher override UI (drag site in/out of route). Never fully automate v1.

## 5. Build framework — phases, mechanics, difficulty

Difficulty: ▲ easy · ▲▲ medium · ▲▲▲ hard. Times = you solo, focused.

| Phase | What you build | Mechanics | Difficulty | Time |
|---|---|---|---|---|
| **0. Foundation** | Postgres+PostGIS; split repo into `ingest / model / optimize / api / ui`; FastAPI backend, Streamlit stays as UI | Schemas: sites, containers, trips, stops, visits, photos, weights, complaints, forecasts | ▲ | 1–2 wk |
| **1. Ingestion** | Parsers: GPS traces (Wialon/Teltonika formats are the KZ norm), weighbridge CSV/1С export, site registry, iKomek feed, weather | Idempotent batch jobs; data-quality report per source | ▲▲ | 2–4 wk build; **elapsed time gated by data access, not code** |
| **2. Ground truth** | a) Stop detection: map-match GPS, dwell ≥60–90s within ~40m of site → visit events. b) Photo→fill CV pipeline. c) NNLS weight disaggregation | movingpandas/OSRM-match; VLM bootstrap → fine-tune; scipy.optimize.nnls | ▲▲▲ (messiest phase) | 4–6 wk |
| **3. Prediction** | v0 shrunken rates + accumulation + quantiles → forecast API; backtest harness | Empirical Bayes; rolling-origin eval; overflow-risk output | ▲ (v0) then ▲▲ (v1 LightGBM after ~2–3 mo of labels) | 1–2 wk (v0), +3–4 wk (v1) |
| **4. Routing** | OSRM matrix + OR-Tools CVRP with capacity/shift/dump-trips/must-serve; route sheets export | Self-host OSRM; capacity dimension + refill nodes | ▲▲ | 2–3 wk |
| **5. Pilot loop** | Dispatcher view (have it) + driver channel: Telegram bot or PWA — confirm visit, flag "overflowing/half-empty" (extra labels); weekly retrain; A/B protocol | One district on EcoRoute plan, comparable district on fixed routes; measure 4 KPIs above for 8–12 wk | ▲▲ tech, ▲▲▲ adoption | 2–3 wk build + pilot duration |

**Critical path:** data access agreement → Phase 1 → Phase 2. Everything else is parallelizable and honestly not hard for you.

**Realistic totals** (you, solo, competent, using AI tools):
- Pilot-ready v1 (Phases 0–4, model v0): **~3–4 months full-time** from the day you get data. 5–7 months alongside studies.
- Validated pilot with measured savings: **+3 months** of pilot runtime.
- Model v1 (real ML beating baseline): meaningful around **month 4–6**, once labels accumulate.

## 6. Ranked by what will actually hurt

1. **Data access & politics** (▲▲▲, months, not code) — the accelerator's job; push for a data-sharing MoU with Астана Тазалық as milestone #1.
2. **Ground-truth construction** (Phase 2) — GPS is dirty, photos have snow/night/angle issues, weights are truck-level. Budget the most debugging here.
3. **Driver/dispatcher adoption** — where most govtech pilots die. Telegram bot beats a custom app; route sheets must print.
4. **CVRP routing** — sounds hard, is actually the best-documented part (OR-Tools). ▲▲.
5. **The ML itself** — genuinely NOT the bottleneck. v0 is a week. Don't over-engineer it; overflow risk + honest quantiles beat a fancy model with fake certainty.

## 7. Reposition, don't discard, the demo

Keep the Streamlit app as the **simulation sandbox / digital twin**: same UI, but "synthetic mode" vs "live mode" toggle. Jury sees the product vision; pilot fills the live mode. And retire these claims:
- "R² 0.929" → "pipeline validated end-to-end; accuracy will be measured against driver-photo ground truth in the pilot"
- "87.4% / 266 km saved" → "12–25% fleet-km reduction is the credible target (meta-analysis: ~12% real-world avg; best cases 30%+); at Астана Тазалық fleet scale that is still hundreds of thousands of km/year"
- "works without sensors" → sharpen to: "works on the GPS, site registry, and driver photos the operator already collects — sensors optional later"

## Sources

- [Вечерняя Астана — GPS следит за вывозом мусора](https://www.vechastana.kz/gps-sledit-za-vyvozom-musora/) — GPS on trucks, ИС «Waste Management», digital site map
- [Azattyq Ruhy — Мусоровозы под контролем GPS](https://rus.azattyq-ruhy.kz/news/87203-musorovozy-pod-kontrolem-gps-kak-v-astane-perekhodit-na-tsifrovoi-vyvoz-musora) — dispatch control at Астана Тазалық, driver before/after photos, complaint reduction
- [MDPI Logistics — IoT routing optimization meta-analysis](https://www.mdpi.com/2305-6290/9/4/161) — 21.5% avg distance reduction; 12.4% real-world vs 39.8% simulation
- [Sensoneo — municipal case studies](https://www.sensoneo.com/success-stories/new-waste-collection-routes/) — 43–63% cost savings; bins collected at 24–45% full
