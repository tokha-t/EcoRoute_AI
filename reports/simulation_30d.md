# 30-day predictive collection simulation

> **SIMULATED DATA.** This proves policy logic on a modeled Baikonur district; real savings must be measured during the pilot.

World: 250 sites; real OSM records: 74; street-synthesized: 176.

Distance savings are never presented alone: overflow events and max-interval violations are shown in the same table.

| Policy | km_total | Δ vs fixed | overflow_events | max_interval_violations |
|---|---:|---:|---:|---:|
| fixed | 5289.97 | +0.0% | 298 | 24 |
| fixed_naive | 32081.36 | +506.5% | 298 | 24 |
| predictive | 5815.76 | +9.9% | 199 | 0 |
| predictive_reds_only | 3971.32 | -24.9% | 242 | 0 |

## All KPIs

| KPI | fixed | fixed_naive | predictive | predictive_reds_only |
|---|---:|---:|---:|---:|
| km_total | 5289.97 | 32081.36 | 5815.76 | 3971.32 |
| km_per_tonne | 8.41 | 50.98 | 9.27 | 6.48 |
| overflow_events | 298.00 | 298.00 | 199.00 | 242.00 |
| overflow_site_days | 39.73 | 39.73 | 26.53 | 32.27 |
| mean_fill_at_pickup | 44.03 | 44.03 | 44.19 | 74.41 |
| max_interval_violations | 24.00 | 24.00 | 0.00 | 0.00 |
| truck_hours | 591.35 | 1621.25 | 638.08 | 360.60 |
| fuel_liters | 1851.49 | 11228.48 | 2035.52 | 1389.96 |
| co2_kg | 4961.99 | 30092.32 | 5455.19 | 3725.10 |
| cost_kzt | 546189.74 | 3312400.58 | 600477.59 | 410038.58 |
| sites_served | 6450.00 | 6450.00 | 6414.00 | 3755.00 |

## Predictive delta vs fixed

| KPI | Delta |
|---|---:|
| km_total | +9.9% |
| km_per_tonne | +10.2% |
| overflow_events | -33.2% |
| overflow_site_days | -33.2% |
| mean_fill_at_pickup | +0.4% |
| max_interval_violations | -100.0% |
| truck_hours | +7.9% |
| fuel_liters | +9.9% |
| co2_kg | +9.9% |
| cost_kzt | +9.9% |
| sites_served | -0.6% |
