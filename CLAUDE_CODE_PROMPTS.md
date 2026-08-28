# Claude Code prompt pack — EcoRoute AI V1

How to use (this matters more than the prompts):
1. Copy `SPEC_V1_PRE_DATA_MVP.md` into the repo as `docs/SPEC_V1.md`, and put the CLAUDE.md below in repo root. Commit both. The spec becomes the source of truth Claude Code can read itself; prompts stay short.
2. Run **one prompt per session** (milestones M0→M5, in order). Start each milestone in a fresh session (`/clear`). Don't stack milestones in one conversation.
3. After each milestone: review the diff, run the verify commands yourself, commit. Never let it continue onto the next milestone in the same session "while it's at it."
4. If it goes sideways: stop, `/clear`, re-run the same prompt adding one line about what went wrong. Cheaper than steering a confused session.

---

## Step 1 — CLAUDE.md (paste into repo root, commit first)

```markdown
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
```

---

## M0 — Hygiene + max-interval rule

```text
Read docs/SPEC_V1.md sections 6.4 and 6.6, then do exactly this milestone:

1. Create tests/__init__.py so `python -m pytest tests/ -q` discovers tests.
2. Implement the max-interval rule in src/predict.py:
   - New constant MAX_INTERVAL_DAYS = 3.
   - In assign_priority(): any row with hours_since_collection >= MAX_INTERVAL_DAYS*24
     gets priority "Critical" and a new boolean column must_serve=True, regardless of
     predicted fill. All other selected rows must_serve=True as well; skipped rows False.
3. Make the route planning path include all must_serve rows (today: whatever app.py
   passes to routing must be the must_serve set, not just threshold-selected).
4. Extract the CSS blob from app.py into assets/styles.css, loaded at startup.
   No visual changes — pure move.
5. Add tests: max-interval forces Critical at low predicted fill; threshold logic
   unchanged from existing tests; CSS file loads without exception.

Do NOT: touch src/data_generator.py logic, add dependencies, restructure folders.

Verify (run these, show me output):
- python -m pytest tests/ -q          → all pass
- streamlit run app.py headless smoke: python -c "import app"  (no import errors)
Definition of done: tests green + a bin at 10% fill but 4 days overdue appears as
Critical in the plan.
```

## M1 — OSRM road distances

```text
Read docs/SPEC_V1.md section 6.3 (Distances part). Milestone:

1. New module src/optimize/distances.py:
   - get_matrix(points, mode) -> seconds+meters matrices.
   - Primary: OSRM /table API on http://localhost:5000 (assume Docker:
     osrm-backend with kazakhstan-latest.osm.pbf; write docs/osrm-setup.md
     with the exact docker commands for me to run).
   - Fallback: haversine * 1.4 detour factor when OSRM is unreachable;
     return a flag fallback_used=True.
   - Cache matrices to data/cache/ keyed by hash of coordinates set.
2. Wire the existing route distance display in app.py to use road meters when
   available; show a small badge "road distances (OSRM)" vs "straight-line est."
3. Tests: haversine fallback correctness on known coordinate pairs; cache hit path;
   OSRM path mocked with a canned /table JSON response (no network in tests).

Do NOT: replace the routing algorithm yet (that's next milestone), download the
.pbf yourself, or add OSRM client libraries — plain HTTP via requests/httpx.

Verify: python -m pytest tests/ -q; then with OSRM down, app still runs and shows
the fallback badge.
```

## M2 — OR-Tools CVRP

```text
Read docs/SPEC_V1.md section 6.3 (Solver part). Milestone:

1. New module src/optimize/solver.py using ortools:
   plan_routes(sites_df, trucks, params) -> list[Route], each Route = ordered site
   ids + load_kg + duration_s + distance_m; plus violations list.
   Constraints: per-truck capacity_kg, max shift_duration_s, must_serve sites are
   mandatory, non-must_serve sites optional (droppable with penalty), depot
   start/end, 1–3 trucks.
   Estimate site load as containers * capacity_liters * fill_pct * DENSITY_KG_PER_L
   (module constant, default 0.12, documented).
2. Distances come from src/optimize/distances.get_matrix (M1).
3. Keep src/routing.py untouched as legacy; app.py gets a toggle
   "Engine: Classic (2-opt) / CVRP (OR-Tools)" defaulting to CVRP.
4. Comparison panel: same selected set solved by both engines, show km difference.
5. Tests: capacity never exceeded; must_serve always present in solution; solver
   completes 300 random sites / 2 trucks in < 30s; empty selection handled.

Do NOT: implement landfill mid-route dumps (P1, separate task), multi-day planning,
or remove the legacy engine.

Verify: python -m pytest tests/ -q; demo run with 2 trucks where total demand
exceeds one truck's capacity → solver uses both trucks, no violations.
```

## M3 — photo_fill module

```text
Read docs/SPEC_V1.md section 6.2 fully. Milestone:

1. src/photo_fill/dataset.py: ingest tooling for my field photos —
   CLI: python -m src.photo_fill.dataset add <folder> --site-id S --label full
   Stores to data/photos/{site_id}/{timestamp}.jpg + appends data/photos/labels.csv
   (site_id, filename, ts, label, labeler). Validate labels ∈ {empty, half, full, overflowing}.
2. src/photo_fill/estimator.py:
   estimate_fill(image_path) -> {"cls": str, "pct_range": tuple, "confidence": float}
   v0 backend: Anthropic API vision call (model claude-haiku, structured JSON output;
   read ANTHROPIC_API_KEY from env; never hardcode). Strict JSON schema parsing with
   one retry on malformed output. confidence < 0.6 → cls="uncertain".
3. src/photo_fill/evaluate.py:
   CLI producing accuracy, macro-F1, per-class confusion matrix — with GROUP SPLIT BY
   site_id (no site appears in both train/tune and test). Output markdown report to
   reports/photo_eval.md.
4. Tests: dataset CLI round-trip; estimator with mocked API response; by-site split
   property (assert no site overlap); uncertain path.

Do NOT: train a local model yet (only if VLM v0 < 85% later), build UI (next
milestone), commit any real photos (add data/photos/ to .gitignore, keep labels.csv).

Verify: python -m pytest tests/ -q; end-to-end on 3 sample images I'll drop in
data/photos_inbox/ (use placeholder images if absent).
```

## M4 — App: mode toggle + live photo flow

```text
Read docs/SPEC_V1.md sections 6.5 and 7 (acceptance checks). Milestone:

1. app.py sidebar: mode toggle "Simulation" | "Live photo".
   Simulation = current sandbox, with a persistent visible banner
   "Simulated data — for demonstration" on every savings/metric element.
2. Live photo mode: st.file_uploader (multi) → photo_fill.estimate_fill on each →
   editable result table (site picker from bins.csv, class, confidence) →
   [Add to today's plan] → selected sites merge into the CVRP plan (M2) →
   map + route recompute. Uncertain results visually flagged, excluded by default.
3. Savings copy: replace any 87%/R² framing with "target range 15–25% (industry
   real-world deployments)"; keep exact demo numbers but always labeled simulated.
4. Confusion-matrix page: render reports/photo_eval.md inside the app (tracker demo).
5. Tests: pure-logic helpers extracted from app.py get unit tests (merge logic,
   banner presence flag). UI itself smoke-tested via `python -c "import app"`.

Do NOT: add auth, DB, FastAPI, or camera hardware integration; do not autoplan
without a user click.

Verify: python -m pytest tests/ -q; manual script docs/demo_script.md updated with
the exact 3-minute click path (write it).
```

## M5 — Hardening + dry run

```text
Final QA milestone. Read docs/SPEC_V1.md section 3 (goals) and 7.

1. GitHub Actions: .github/workflows/ci.yml running pytest on push (python 3.11+).
2. Offline resilience: app must fully start with no network (OSRM down, no API key)
   — photo mode shows a clear "API key required" state instead of crashing.
3. Determinism: seed everything; two consecutive simulation runs give identical numbers.
4. Sweep TODOs, dead code, unused imports; ruff check --fix (add ruff as dev dep).
5. Produce docs/DRYRUN.md: checklist for 3 consecutive clean demo runs (timing each
   step, target < 3 min).

Do NOT add features. Anything found broken: fix minimally, note in docs/DRYRUN.md.

Verify: pytest green in CI; app boots with network disabled; you walk me through
docs/demo_script.md timings.
```

---

## M6 — Independent-review follow-ups (half-day session)

```text
Context: an external engineering review flagged a short list of hardening items.
Read docs/SPEC_V1.md section 6.2 for the photo contract. Do exactly these, nothing else:

1. Upload guards in the photo path (app.py estimate_uploaded_photo + src/photo_fill/estimator.py):
   - Extension allowlist: .jpg .jpeg .png .webp (case-insensitive); anything else
     returns a table-safe error row, never raises.
   - File size cap: MAX_UPLOAD_BYTES = 12 * 1024 * 1024, checked before writing temp file.
   - Set PIL Image.MAX_IMAGE_PIXELS to 40_000_000 in estimator module (decompression-bomb guard)
     and catch Image.DecompressionBombError into the same error-row path.
2. src/optimize/distances.py: get_matrix with <2 points must return source="trivial"
   (not "osrm"), fallback_used=False. Update any code/tests that compare source values;
   the UI badge must treat only source=="osrm" as road distances.
3. src/predict.py _load_or_train_model: replace bare `except Exception` with
   (OSError, EOFError, ValueError, AttributeError, ModuleNotFoundError) and log a
   warning with the exception before retraining. Retrain at most once.
4. New src/config.py: move these module constants there and re-import everywhere:
   FUEL_COST_KZT_PER_LITER, FUEL_CONSUMPTION_LITERS_PER_KM, CO2_KG_PER_LITER_DIESEL,
   AVERAGE_TRUCK_SPEED_KMH, STOP_TIME_MINUTES_PER_BIN, DENSITY_KG_PER_L,
   VLM model name, CONFIDENCE_THRESHOLD, MAX_INTERVAL_DAYS, DETOUR_FACTOR.
   Pure move — zero behavior change, all existing imports keep working
   (re-export from original modules is fine).
5. Tests for 1-3 (oversize file, bad extension, bomb-guard path, trivial matrix
   label, retrain-once logging). Run the full suite.

Do NOT: refactor app.py into src/ui/, remove routing.py, add auth, touch the
solver, or change any UI copy.

Verify: python -m pytest tests/ -q green; ruff check . clean.
```

---

## M7 — Pilot readiness: Baikonur demo + Russian UI + real-data import

Context change: a real pilot was offered in Baikonur district, Astana (operator:
ТОО «ГорКомТранс»), target start 1–2 Sept. The demo audience is now the akimat and
a dispatcher, not a hackathon jury. Run these as THREE separate sessions, in order.

### M7a — Baikonur demo data (do first)

```text
Goal: the demo must show Baikonur district, not an abstract Astana blob.

1. New module src/geo/osm_sites.py:
   fetch_container_sites(bbox) -> DataFrame — queries the Overpass API for
   amenity=waste_disposal and amenity=recycling nodes/ways (way -> use center),
   returns id, lat, lon, tags. Cache the raw response to data/cache/osm_sites.json;
   if Overpass is unreachable, fall back to the cached file, and if that is absent
   raise a clear error. Include a CLI: python -m src.geo.osm_sites --bbox ...
2. Also fetch real place names: query Overpass for place=suburb/neighbourhood
   inside the same bbox, so demo sites carry REAL microdistrict names.
   Do NOT invent district names.
3. src/data_generator.py: add generate_bins_for_area(sites_df, place_names, seed)
   that builds the demo snapshot on REAL coordinates and REAL place names,
   keeping every existing column so predict/solver/app work unchanged.
   Keep the old generator working for tests.
4. If Overpass returns fewer than 40 sites in the Baikonur bbox (likely — OSM
   coverage of container pads is thin), synthesize the remainder along real
   residential streets from OSM instead of random offsets, and record in the
   returned DataFrame a boolean column `source_real` so the UI can state
   honestly how many sites are real OSM records vs. placeholders.
5. Baikonur bbox to use as default: south=51.13, west=71.34, north=51.22, east=71.47
   (verify against the OSM relation for Байқоңыр ауданы if you can fetch it).
6. Tests: mocked Overpass response, cache fallback path, source_real accounting,
   generated frame passes the existing predict + solver contracts.

Do NOT: change the UI yet, remove the synthetic generator, or hardcode names.
Verify: pytest green; CLI prints how many real sites were found.
```

### M7b — Russian UI + route sheet export

```text
Audience is a Russian-speaking dispatcher and akimat officials.

1. New src/i18n.py: simple dict-based translations {key: {"ru": ..., "en": ...}},
   t(key, lang) helper. No gettext, no new dependency.
2. Sidebar language switch RU | EN, default RU. Translate every user-visible
   string in app.py: headers, metric labels, priority names
   (Critical->Критическая, High->Высокая, Medium->Средняя, Skip->Пропустить),
   buttons, the simulated-data banner, error and empty states.
   Keep code, comments, and column names in English.
3. New src/reports/route_sheet.py: build_route_sheet(plan, sites_df, lang)
   -> printable маршрутный лист per truck: date, truck id, ordered stops with
   address + container count + predicted fill + priority, cumulative km,
   estimated shift time, signature lines for driver and dispatcher.
   Export as CSV and as printable HTML (no new PDF dependency — the browser
   prints the HTML). Download buttons in the app.
4. Tests: i18n has no missing keys in either language (assert key sets match),
   route sheet contains every must-serve site, stop order matches the plan.

Do NOT: translate code identifiers, add a PDF library, or restructure app.py.
Verify: pytest green; switch to RU and confirm no English leaks on the main screen.
```

### M7c — Real registry import

```text
Goal: when the operator sends their Excel, it loads the same day.

1. New src/ingest/registry.py:
   load_registry(path) reads CSV or XLSX (openpyxl is allowed) matching
   pilot/shablon_ploshadki.csv columns:
   id_ploshadki, adres, shirota, dolgota, kolichestvo_konteynerov,
   obem_konteynera_l, tip_zastroyki, grafik_vyvoza, primechanie.
   - Tolerant column matching (case, spaces, common RU synonyms).
   - Rows without coordinates are kept and flagged needs_geocoding=True
     (do NOT auto-geocode; just report the count).
   - Returns (sites_df in the internal schema, report) where report lists
     row count, missing fields, duplicates, out-of-bbox coordinates.
2. App: "Импорт реестра" uploader in the sidebar → shows the validation report →
   on confirm, replaces the demo snapshot for the session. Never overwrite
   data/bins.csv silently; write to data/imported_sites.csv.
3. When imported data is active, the simulated-data banner must switch to
   "данные оператора" — the honesty invariant works in both directions.
4. Tests: template file parses, messy variants (extra spaces, RU synonyms,
   missing coords, duplicate ids) produce the right report, internal schema
   is valid for predict + solver.

Do NOT: add a database, geocode automatically, or accept files >20 MB.
Verify: pytest green; pilot/shablon_ploshadki.csv imports cleanly.
```

---

## V2 simulation — S1…S5 (run one per session, in order)

Source of truth: `docs/SPEC_V2_SIMULATION.md`. Copy it into the repo first and commit.
Each session: read the named spec sections, implement, test, stop. Do not run ahead.

### S1 — Synthetic world on real coordinates

```text
Read docs/SPEC_V2_SIMULATION.md sections 2 and 3. Implement src/sim/world.py only.

- Overpass queries for container sites, residential street geometry, place names, building
  tags (one module src/geo/overpass.py, plain HTTP via requests, no new deps).
- Cache every raw Overpass response under data/cache/osm/*.json; if the API is unreachable,
  load from cache; if cache is empty, raise a clear error naming the fix.
- generate_world(seed, n_sites=250, bbox=BAIKONUR) -> DataFrame with every field in §2.1.
- Fill-rate formula exactly as §3.5; weekday factors as §3.6 exposed as a function.
- Deterministic: same seed + same cache => identical DataFrame (assert in tests).
- CLI: python -m src.sim.world --out data/world.csv  (prints real vs synthesized counts).

Do NOT touch app.py, the solver, or existing predict.py in this session.
Verify: pytest green; CLI writes a CSV with >=200 rows and real district names.
```

### S2 — Fill model and classification

```text
Read docs/SPEC_V2_SIMULATION.md section 4. Implement src/sim/fill.py only.

- advance_day(world_df, day, rng) -> world_df with fill_pct increased per §4.1.
  Fill is NOT clipped at 100; overflow is a real state that must be countable.
- classify(world_df, day, params) -> adds columns: klass (GREEN|YELLOW|RED),
  reason (max_interval|high_fill|overflow_predicted|none), must_serve (bool),
  projected_fill_pct.
- Rule precedence exactly as §4.2. All thresholds from src/config.py, no literals.
- Tests must include the three worked examples in §8 acceptance criteria, plus:
  boundary at exactly RED_THRESHOLD, exactly MAX_INTERVAL_DAYS, H=3 horizon,
  and that a GREEN site overdue past max interval is RED with reason max_interval.

Do NOT wire this into the app yet; do not modify src/predict.py (legacy stays).
Verify: pytest green.
```

### S3 — Routing with landfill dump trips

```text
Read docs/SPEC_V2_SIMULATION.md section 5. Extend src/optimize/solver.py.

- Add landfill node support: a route may visit the landfill multiple times; each visit resets
  the load dimension to zero and costs LANDFILL_SERVICE_SECONDS plus travel.
  Use OR-Tools reload/refill node modelling (duplicate landfill nodes, capacity dimension
  slack reset). Document the approach in the module docstring.
- YELLOW handling per §5.3 — this is the core product rule, implement it exactly:
  two-pass solve, Pass 1 = RED only to compute reference_cost (meters per m³),
  Pass 2 = RED mandatory + YELLOW as droppable optional nodes with
  drop_penalty = volume_m3 * reference_cost * YELLOW_TOLERANCE (default 1.0, config).
  Fall back to FALLBACK_COST_PER_M3_M when Pass 1 has zero volume.
  Record for every yellow: insertion_cost, penalty, served/skipped, and a Russian
  explanation string for the UI.
- A `reds_only` flag routes RED alone (analysis mode, not the default).
- Preflight infeasibility checks per §5.4 returning named, human-readable Russian messages.
- Plan object gains: dump_stops per route, served_yellow, skipped_yellow (with reasons),
  unserved_red, reference_cost, mode.
- Tests: capacity respected between dumps; a fleet whose demand is 2.5x capacity produces
  routes with dump trips rather than an infeasibility; every RED appears exactly once;
  a yellow on the path is served; the same yellow moved 3 km away with small volume is
  skipped; a large-volume yellow at 3 km is served (penalty scales with volume);
  dropping yellow never causes an unserved RED; infeasible case returns a report naming
  the site.

Do NOT change the UI in this session.
Verify: pytest green; 250 sites / 3 trucks solves under 30 s.
```

### S4 — 30-day simulation engine and report

```text
Read docs/SPEC_V2_SIMULATION.md section 6. Implement src/sim/run.py.

- simulate(world, policy, days=30, seed) -> DailyRecord list, for policies:
  "fixed" (calendar schedule, same CVRP solver), "fixed_naive" (bin_id order routing),
  "predictive" (S2 classification + S3 default yellow rule), and
  "predictive_reds_only" (S3 reds_only flag, for contrast).
- Every KPI in the §6 table, per day and aggregated.
- Writes reports/simulation_30d.md (summary table + per-policy deltas) and
  reports/simulation_30d.csv (daily rows).
- The report MUST print overflow_events and max_interval_violations next to any km saving,
  per §6. Add a test asserting the report contains all three.
- CLI: python -m src.sim.run --days 30 --seed 42

Do NOT add charts here (UI session next). Keep runtime under 5 minutes.
Verify: pytest green; report generated; predictive shows 0 max-interval violations.
```

### S5 — Map, routes and day controls in the app

```text
Read docs/SPEC_V2_SIMULATION.md section 7. Fix and rebuild the map UI.

1. FIRST diagnose why the current map does not render on Streamlit Cloud — check the
   Plotly map style (mapbox styles needing a token vs open styles), and report the cause
   before fixing. Use an open style requiring no token, or pydeck.
2. Site markers coloured by klass, sized by capacity, hover per §7.1.
3. Per-truck route polylines using OSRM /route geometry when available, straight segments
   labelled "прямые линии" otherwise. Depot and landfill icons distinct.
4. Truck panel, day slider + "следующий день", "прогон 30 дней" button rendering the report.
5. Policy controls per §7.5 wired to config defaults.
6. Legend states real-vs-synthesized site counts; simulated banner stays.

Do NOT add new dependencies beyond what is already in requirements.txt.
Verify: pytest green; map renders with OSRM stopped; screenshot the 3 policy views.
```

### S6 — Corrections round: empty return, real district polygon, tolerance sweep

Three defects found by review of the first V2 build. Run as ONE session, in this order.
Re-read docs/SPEC_V2_SIMULATION.md §3.1–3.2, §5.2, §6.1 — they were updated 2026-08-28.

```text
1. EMPTY RETURN TO DEPOT (domain bug — trucks currently return to the depot loaded).
   In src/optimize/solver.py constrain the load dimension so CumulVar(End(vehicle)) == 0
   for every vehicle. A truck that collected anything must therefore visit the landfill
   after its last collection stop, before the depot. Trucks that collect nothing stay empty
   and need no landfill visit.
   - Surface the final landfill leg in the Plan (it must appear in route stop order),
     in the route sheet, and on the map.
   - Tests: every vehicle ends with zero load; a one-site route contains a landfill stop
     immediately before the depot return; multi-dump routes still respect capacity between
     dumps; infeasibility (landfill unreachable within shift) returns a named report.

2. REAL DISTRICT POLYGON (the bbox in config is wrong — it covers the old city centre,
   not Baikonur district, which is the northern/north-eastern area plus a 460 ha exclave).
   - src/geo/overpass.py: fetch relation["boundary"="administrative"] named
     Байқоңыр|Байконур inside Astana; build a MULTI-part polygon (keep the exclave);
     cache to data/cache/osm/baikonur_boundary.geojson.
   - src/sim/world.py: filter every generated site by point-in-polygon against it.
     Use a bbox only to narrow the Overpass query, never as the boundary.
   - If the relation is unavailable and no cache exists: raise a clear error. Do NOT
     fall back to a bbox and do NOT invent coordinates.
   - Add --sector <name> to restrict generation to one place=suburb|neighbourhood inside
     the district; default to the densest. The UI must state which sector is displayed.
   - Point-in-polygon with a small pure helper (ray casting) — no new dependency;
     shapely only if it is already installed.
   - Tests: a point in the city centre is rejected; a point in the exclave is accepted;
     cached-polygon path works offline; missing polygon raises, never silently degrades.

3. YELLOW_TOLERANCE SWEEP (§6.1). The current default of 1.0 makes predictive drive 9.9%
   MORE than the fixed baseline — it collects nearly everything. Do not hand-tune it.
   - src/sim/run.py: sweep tolerance over {0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0}, recording
     the full KPI set for each.
   - Report a trade-off table (km_total vs overflow_events per tolerance) and pick the
     default automatically: the largest tolerance beating `fixed` on BOTH km_total and
     overflow_events. If none does, state that plainly in the report and default to the
     km-minimising value.
   - Write the chosen value into src/config.py as YELLOW_TOLERANCE and record the reason
     in the report.
   - Test: the report contains the sweep table and the selection rationale; the chosen
     default is reproducible from the recorded KPIs.

After all three: regenerate reports/simulation_30d.* — every earlier number is invalid
because routes were missing their final landfill leg.

Verify: python -m pytest tests/ -q green; ruff check . clean; report regenerated and its
headline no longer shows predictive losing to fixed on km without explanation.
```

---

## Extras (only when needed)

- **P1 landfill dumps** (after M2 works): "Add optional landfill refill node to solver.py: trucks may visit LANDFILL coords mid-route to reset load when capacity would otherwise be violated. Test: demand 2× capacity, 1 truck → solution contains exactly one dump visit."
- **Local classifier** (only if VLM < 85%): "Fine-tune torchvision MobileNetV3-small on data/photos with augmentation, same by-site eval as M3; keep estimator interface identical; pick backend via PHOTO_BACKEND env."
- **Telegram bot** (days before pilot): "python-telegram-bot: /route sends today's ordered stops; driver taps Done / Overflowing / Half-empty per stop; append to data/visits.csv. No DB."
```
