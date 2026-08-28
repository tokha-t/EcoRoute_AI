# SPEC V2 — Predictive collection simulation (pre-data)

Status: draft for implementation · Author: Tokha · Date: 2026-08-27
Supersedes the demo behaviour in SPEC_V1 §6.4 (prediction) and extends §6.3 (routing).
Everything here runs on **synthetic data on real Astana coordinates**. No operator data required.

---

## 1. Goal

Simulate the full daily decision loop for waste collection in one Astana district and prove,
over a 30-day horizon, that a demand-driven policy beats the current fixed-schedule policy
on measurable KPIs.

Deliverable: a working map + per-truck routes + a 30-day comparison report.

## 2. Core objects

### 2.1 Site (контейнерная площадка)

One record per collection point. A site holds 1..N containers and is one truck stop.

| Field | Type | Meaning |
|---|---|---|
| `site_id` | str | Stable id |
| `lat`, `lon` | float | Real coordinates (OSM-derived, see §3) |
| `address` | str | Human-readable, RU |
| `district` | str | Real microdistrict name |
| `containers` | int | Number of containers |
| `container_liters` | int | Volume of one container (120/240/660/1100) |
| `capacity_liters` | int | `containers * container_liters` |
| `area_type` | enum | `multistorey` \| `private` \| `commercial` \| `mixed` |
| `daily_fill_rate_pct` | float | Individual accumulation rate, % of capacity per day |
| `fill_pct` | float | Current fill, 0..100 (may exceed 100 internally → overflow) |
| `last_service_day` | int | Simulation day index of last emptying |
| `source_real` | bool | True if coordinates came from OSM, False if synthesized on a real street |

### 2.2 Truck

| Field | Meaning |
|---|---|
| `truck_id` | str |
| `capacity_kg` | float |
| `shift_seconds` | float |
| `service_seconds_per_site` | float, default 180 |

### 2.3 Fleet-level constants (`src/config.py`)

`DENSITY_KG_PER_L = 0.12`, depot coords, landfill coords, thresholds (§4), horizon days.

## 3. Synthetic world generation (`src/sim/world.py`)

1. **Coordinates.** Query Overpass for `amenity=waste_disposal` / `amenity=recycling` inside the
   Baikonur bbox (`51.13, 71.34, 51.22, 71.47`). Use every real node found; set `source_real=True`.
2. **Top-up to target count** (default 250 sites): place remaining sites on real residential
   street geometry from OSM (`highway=residential|living_street`), snapped to the road, min 60 m
   apart. `source_real=False`.
3. **Names.** `district` from OSM `place=suburb|neighbourhood`; `address` from the nearest
   `addr:street` + `addr:housenumber` where available, else "ул. X, район Y". Never invent names.
4. **Area type** from surrounding OSM building tags: `building=apartments|residential` dense →
   `multistorey`; `building=house|detached` → `private`; `shop|commercial|office` nearby → `commercial`.
5. **Fill rate per site** — this is the heart of the simulation:
   ```
   base = BASE_RATE[area_type]        # multistorey 42, private 14, commercial 55, mixed 30 (%/day)
   size_factor = 1000 / capacity_liters      # small containers fill faster in % terms
   noise = lognormal(mu=0, sigma=0.22)       # per-site individuality, stable across days
   daily_fill_rate_pct = clip(base * size_factor**0.5 * noise, 3, 95)
   ```
   Store it on the site. It is the site's fixed personality.
6. **Day-of-week multiplier** applied at simulation time, not stored:
   `WEEKDAY_FACTOR = {Mon..Thu: 1.0, Fri: 1.15, Sat: 1.25, Sun: 1.1}` for residential;
   commercial inverted (Sat/Sun 0.6).
7. **Initial state:** `fill_pct` seeded uniform 0..85, `last_service_day` back-dated consistently.
8. Deterministic under a seed. Cache the OSM response so the world regenerates offline.

## 4. Fill model and classification (`src/sim/fill.py`)

### 4.1 Daily accumulation

```
fill_pct(day+1) = fill_pct(day) + daily_fill_rate_pct * weekday_factor(day+1) * jitter
jitter ~ Normal(1.0, 0.08), clipped to [0.75, 1.25]
```
Fill is **not** clipped at 100 internally — values above 100 mean overflow and must be counted
as such. The UI clamps display at 100 and shows an overflow badge.

Emptying a site sets `fill_pct = 0`, `last_service_day = day`.

### 4.2 Classification (exact rules, in priority order)

Let `H = PLANNING_HORIZON_DAYS` (default 1) and
`projected = fill_pct + daily_fill_rate_pct * weekday_factor(next H days, summed)`.

```
1. if days_since_service >= MAX_INTERVAL_DAYS      -> RED   (reason: "max_interval")
2. elif fill_pct >= RED_THRESHOLD (70)             -> RED   (reason: "high_fill")
3. elif projected >= OVERFLOW_LIMIT (100)          -> RED   (reason: "overflow_predicted")
4. elif fill_pct >= YELLOW_THRESHOLD (21)          -> YELLOW
5. else                                            -> GREEN
```

- Every RED site is **must-serve**: it enters today's plan, no exceptions.
- YELLOW sites are candidates only (see §5.3).
- GREEN sites are excluded today.
- Rule 1 overrides everything, including a green fill level. Never remove this rule.
- Every classification result carries its `reason` string, shown in the UI and route sheet.

All thresholds live in `src/config.py` and are exposed as UI sliders.

## 5. Routing (`src/optimize/solver.py`, extend existing)

### 5.1 Node set

`depot` + all sites in today's candidate set + `landfill` (repeatable, see §5.2).

Distances and travel times: OSRM road matrix with the existing haversine fallback, flagged.

### 5.2 Constraints

- **Capacity:** load accumulates per stop as
  `load_kg = capacity_liters * min(fill_pct,100)/100 * DENSITY_KG_PER_L`.
- **Landfill dump trips (new, required):** a truck may visit the landfill node mid-route; doing so
  resets its load to zero and costs `LANDFILL_SERVICE_SECONDS` (default 900) plus travel. Model as
  reload/refill nodes in OR-Tools with multiple visits allowed. A route may contain several dumps.
- **Shift:** total route duration ≤ `shift_seconds`, including service and dump times.
- **Mandatory:** every RED site must appear in exactly one route.
- **Start/end:** depot.

### 5.3 How YELLOW sites are decided (core product rule)

**YELLOW sites are part of the plan by default.** The router serves them together with RED sites
and drops one only when both conditions hold:

1. **Safe to postpone.** Guaranteed by construction: §4.2 rule 3 already promotes to RED any site
   that would reach `OVERFLOW_LIMIT` before the next planned visit. Therefore *every* YELLOW site
   reaching the router can wait at least one cycle. No extra check is needed here — but the router
   must never treat a RED site as droppable.
2. **Skipping yields significant economy.** Serving the site costs more travel per unit of waste
   collected than the fleet's own efficiency on this day's mandatory work.

Condition 2 is made concrete with a **two-pass solve**, so the threshold self-calibrates to the
district instead of relying on a magic constant:

```
Pass 1 — reference: solve with RED sites only.
         reference_cost = total_route_meters / total_volume_m3      # m per m³
Pass 2 — full plan:  RED mandatory, YELLOW optional (droppable).
         drop_penalty(site) = volume_m3(site) * reference_cost * YELLOW_TOLERANCE
```

`YELLOW_TOLERANCE` defaults to `1.0` and is a UI slider (0.5 = collect yellow only when clearly
cheap; 2.0 = collect yellow eagerly). OR-Tools drops an optional node exactly when its insertion
cost exceeds its penalty, so this makes the rule literally: *serve a yellow bin when the extra
distance per m³ is no worse than what we are already paying per m³ today; skip it when it is worse.*

Consequences that must hold and be tested:
- A yellow bin directly on a truck's path is essentially always served (insertion cost ≈ 0).
- A yellow bin requiring a long detour for a small volume is skipped.
- A **large** yellow bin justifies a longer detour than a small one, because the penalty scales
  with volume.
- Dropping a yellow site is never allowed to be the reason a RED site goes unserved.
- If Pass 1 is infeasible, report per §5.4 and do not attempt Pass 2.
- If `total_volume_m3` in Pass 1 is zero (no RED sites today), fall back to the constant
  `FALLBACK_COST_PER_M3_M` (default 1200 m per m³) for the penalty.

Each skipped yellow site is recorded with its computed insertion cost and penalty so the UI and
route sheet can explain *why* it was skipped ("детур 1.8 км на 0.4 м³ — дороже средней стоимости
вывоза сегодня"). Explainability here is a product feature, not a debug aid.

**Comparison mode (analysis only, not the default):** a `reds_only` toggle routes RED sites alone.
Used in the 30-day run to show how much value comes from opportunistic yellow collection.

### 5.4 Infeasibility

Before solving, run preflight checks and, if violated, return an explicit report instead of a
silently truncated plan:

- total RED load > fleet capacity × max dump trips → `"Требуется дополнительная машина или рейс"`
- a single RED site load > largest truck capacity → name the site
- a RED site unreachable within the shift → name the site

The UI must show these in red text with a suggested fix (add truck / extend shift / lower threshold).

### 5.5 Output

`Plan` object per day: routes per truck (ordered stops, load, distance, duration, dump stops),
unserved RED list (must be empty for a valid plan), served YELLOW list, totals, distance source.

## 6. 30-day simulation engine (`src/sim/run.py`) — the key deliverable

Run two policies over the same world, same seed, same weather/weekday sequence:

- **Policy `fixed`** (baseline = current practice): every site is serviced on a fixed calendar,
  e.g. `multistorey` daily, `private` every 3rd day, `commercial` daily — configurable. Routing for
  the baseline uses the same CVRP solver so the comparison isolates *what is collected*, not
  *how well it is routed*. Also record a `fixed_naive` variant routed in bin_id order, to show
  the routing gain separately.
- **Policy `predictive`** (the product): classification per §4, routing per §5 — RED mandatory,
  YELLOW served unless skipping is economical (§5.3).
- **Policy `predictive_reds_only`** (analysis): same classification, YELLOW never served. Shows how
  much the opportunistic yellow rule contributes, and whether it prevents future overflow.

For each day record:

| KPI | Definition |
|---|---|
| `km_total` | Sum of route distances (road km) |
| `km_per_tonne` | km_total / tonnes collected |
| `overflow_events` | Count of sites whose `fill_pct` exceeded 100 at any point that day |
| `overflow_site_days` | Same, normalized per 1000 site-days |
| `mean_fill_at_pickup` | Mean `fill_pct` of serviced sites at service moment |
| `max_interval_violations` | Sites exceeding MAX_INTERVAL_DAYS (must be 0 for predictive) |
| `truck_hours` | Sum of route durations |
| `fuel_liters`, `co2_kg`, `cost_kzt` | From existing savings constants |
| `sites_served` | Count |

Output: `reports/simulation_30d.md` + a CSV of daily rows + a summary table with the percentage
delta of predictive vs fixed for every KPI. **Do not report a headline savings number without
also reporting overflow_events and max_interval_violations** — a policy that saves km by letting
bins overflow is a failure, and the report must make that visible.

## 7. UI requirements (`app.py`)

1. **Map (must work — currently broken on the deployed build).** Plotly/PyDeck map centred on the
   generated district, one marker per site coloured by class: GREEN `#16a34a`, YELLOW `#eab308`,
   RED `#dc2626`. Marker size by `capacity_liters`. Hover shows: address, fill %, daily rate,
   days since service, class, reason.
2. **Route overlay:** one polyline per truck in distinct colours, following the road geometry from
   OSRM `route` service (fallback: straight segments, labelled). Depot and landfill as distinct icons.
3. **Truck panel:** per truck — assigned stop count, load vs capacity bar, distance, duration,
   number of dump trips.
4. **Day controls:** day slider / "next day" button that advances the simulation, plus a "run 30 days"
   button that produces the comparison report.
5. **Policy controls:** mode A/B toggle, thresholds (green/yellow/red), horizon days, max interval,
   truck count and capacity.
6. **Honesty:** the simulated-data banner stays; the map legend states how many sites are
   `source_real=True` vs synthesized.
7. RU/EN language switch (M7b) applies to all new strings.

## 8. Acceptance criteria

- [ ] World generates ≥200 sites inside the Baikonur bbox with real district names; report states
      how many coordinates are real OSM records.
- [ ] Classification unit tests cover every rule in §4.2 including precedence:
      a 10%-full site 5 days unserviced is RED with reason `max_interval`;
      a 51%-full site with 51%/day rate and H=1 is RED with reason `overflow_predicted`;
      a 51%-full site with 10%/day rate is YELLOW.
- [ ] No plan is returned containing an unserved RED site; infeasibility returns a named report.
- [ ] Capacity is never exceeded on any route, including between dump trips.
- [ ] Yellow rule (§5.3): a yellow site on a truck's path is served; the same site moved 3 km away
      with small volume is skipped; a large-volume yellow at that distance is served. Every skipped
      yellow carries a recorded insertion cost and penalty.
- [ ] A 30-day run completes in under 5 minutes on a laptop and writes the report.
- [ ] In the 30-day report, `predictive` shows `max_interval_violations = 0` and
      `overflow_events` no worse than `fixed`.
- [ ] Map renders locally and on Streamlit Cloud with no OSRM available (fallback path).
- [ ] `python -m pytest tests/ -q` green; `ruff check .` clean.

## 9. Non-goals

- No real operator data, no data ingestion pipeline (that is the pilot).
- No machine-learned fill model — the rate is a simulation parameter now and will be *estimated
  from history* in the pilot. Do not train a model on synthetic data and quote its accuracy.
- No multi-day lookahead optimization (single-day horizon + max-interval rule is the approximation).
- No database, no auth, no FastAPI. Streamlit monolith stays.
- No traffic-aware or time-window routing.

## 10. Honesty rules for this simulation

Every number produced here is simulated and must be labelled so. The 30-day comparison proves the
**policy logic works**, not that Astana will save that amount. When presenting: "на смоделированном
районе с реальными координатами политика даёт X% меньше пробега; реальную цифру измеряем на пилоте."
