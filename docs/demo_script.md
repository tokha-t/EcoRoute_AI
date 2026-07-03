# EcoRoute AI — 3-Minute Demo Script

Exact click path for the tracker demo (SPEC_V1 §7: demo runs offline-tolerant, < 3 min).
Rehearse until each block lands on time; three consecutive clean dry runs before the real one.

## Pre-demo checklist (before anyone is watching)

- [ ] `export ANTHROPIC_API_KEY=...` in the shell that launches the app (live photo mode needs it; simulation does not).
- [ ] Optional: OSRM Docker container up (`docs/osrm-setup.md`). If it is down the app falls back to straight-line estimates and labels it — say so, don't hide it.
- [ ] `streamlit run app.py` — let it fully load once (first run trains the demo model).
- [ ] 2–3 container photos ready in a folder (e.g. `data/photos_inbox/`). Naming a file after a site (`BIN-0042.jpg`) pre-fills the site picker.
- [ ] Sidebar reset: Mode = Simulation, threshold 75%, CVRP engine, 2 trucks, 5,000 kg, 8 h.

## 0:00–0:20 — Framing (talk over the loaded Simulation dashboard)

Say: "Cities collect waste on fixed schedules — trucks visit empty bins and miss overflowing ones.
EcoRoute AI plans each shift from predicted fill levels instead."

Point at the **"Simulated data — for demonstration"** banner: "Everything on this screen is a
synthetic sandbox — we label it, honestly, on every metric. Real deployments target 15–25%
distance savings; that's the range we sell, not these demo numbers."

## 0:20–1:20 — Simulation mode (the sandbox)

1. Point at the KPI row: bins skipped, km / fuel / CO₂ saved — all banner-labeled simulated.
2. Scroll to the map: "One Astana dispatch zone, 180 sites. Faint dots are skipped bins;
   each colored line is one truck's CVRP route — capacity and shift limits respected."
3. Sidebar: move **Collection threshold** 75 → 85 and back. Plan, map, and KPIs recompute live.
4. Point at **Engine comparison**: "Classic 2-opt drives one impossible loop; OR-Tools CVRP
   splits it across 2 trucks of 5 t — the extra km is the price of a plan a driver can execute."
5. One sentence on the max-interval rule: "Any site overdue 3+ days is forced into the route
   no matter what the model says — no bin ever rots because a prediction was wrong."

## 1:20–2:30 — Live photo mode (the real AI)

1. Sidebar → **Mode → Live photo**. Say: "No sensors needed — a phone photo is the sensor."
2. **Browse files** → select the 2–3 prepared photos (multi-select). Each returns a class +
   confidence from the vision model in a few seconds.
3. Walk the editable table: "Class and site are editable — dispatcher's local knowledge beats
   the model. This ⚠️ low-confidence row is **excluded by default**; nothing uncertain enters
   the plan silently. I resolve it by picking the class myself after looking at the photo."
4. Assign sites to the confident rows (pre-filled if files are named after sites).
5. Click **Add to today's plan** → CVRP recomputes → map + route order render.
   Say: "Fill levels here are live model output; only the site registry is still the demo one."

## 2:30–3:00 — Evaluation honesty + close

1. Stay in Live photo mode → **Photo model evaluation** tab: per-class confusion matrix,
   accuracy, macro-F1 — evaluated with a **by-site split** so near-duplicate photos can't
   inflate the score. (If no report is generated yet, the page shows exactly how to produce it —
   that transparency is the point.)
2. Close: "V1 ships this as a shadow pilot for one district: our plans vs. their routes,
   measured in km. The 15–25% target comes from real deployments, and every simulated number
   you saw today was labeled as such."

## Failure playbook

- **OSRM down** → badge says "straight-line est." — acknowledge it, keep going.
- **No API key / no network in live mode** → photo rows show an error + "Retry failed photos";
  fall back to narrating the flow over the editable table with the Simulation-mode map.
- **CVRP infeasible** (too much load for the fleet) → the app says exactly why; add a truck in
  the sidebar and let it recompute — that's a feature moment, not a bug.
