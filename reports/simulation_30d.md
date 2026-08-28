# 30-day predictive collection simulation

> **SIMULATED DATA.** This proves policy logic on a modeled Baikonur district; real savings must be measured during the pilot.

World: 250 sites in sector Өндіріс; real OSM records: 13; street-synthesized: 237.

Distance source: road_cache.

Distance savings are never presented alone: overflow events and max-interval violations are shown in the same table.

| Policy | km_total | Δ vs fixed | overflow_events | max_interval_violations |
|---|---:|---:|---:|---:|
| fixed | 5561.95 | +0.0% | 237 | 50 |
| fixed_naive | 34211.54 | +515.1% | 237 | 50 |
| predictive | 4818.34 | -13.4% | 171 | 0 |
| predictive_reds_only | 4818.34 | -13.4% | 171 | 0 |

## All KPIs

| KPI | fixed | fixed_naive | predictive | predictive_reds_only |
|---|---:|---:|---:|---:|
| km_total | 5561.95 | 34211.54 | 4818.34 | 4818.34 |
| km_per_tonne | 10.03 | 61.70 | 8.92 | 8.92 |
| overflow_events | 237.00 | 237.00 | 171.00 | 171.00 |
| overflow_site_days | 31.60 | 31.60 | 22.80 | 22.80 |
| mean_fill_at_pickup | 46.53 | 46.53 | 70.30 | 70.30 |
| max_interval_violations | 50.00 | 50.00 | 0.00 | 0.00 |
| truck_hours | 490.62 | 1141.23 | 366.31 | 366.31 |
| fuel_liters | 1946.68 | 11974.04 | 1686.42 | 1686.42 |
| co2_kg | 5217.11 | 32090.42 | 4519.60 | 4519.60 |
| cost_kzt | 574271.75 | 3532341.45 | 497493.19 | 497493.19 |
| sites_served | 5480.00 | 5480.00 | 3562.00 | 3562.00 |

## Predictive delta vs fixed

| KPI | Delta |
|---|---:|
| km_total | -13.4% |
| km_per_tonne | -11.1% |
| overflow_events | -27.8% |
| overflow_site_days | -27.8% |
| mean_fill_at_pickup | +51.1% |
| max_interval_violations | -100.0% |
| truck_hours | -25.3% |
| fuel_liters | -13.4% |
| co2_kg | -13.4% |
| cost_kzt | -13.4% |
| sites_served | -35.0% |

## YELLOW_TOLERANCE trade-off sweep

![Distance versus overflow frontier](yellow_tolerance_frontier.svg)

| tolerance | km_total | km_per_tonne | overflow_events | overflow_site_days | mean_fill_at_pickup | max_interval_violations | truck_hours | fuel_liters | co2_kg | cost_kzt | sites_served |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 4818.34 | 8.92 | 171.00 | 22.80 | 70.30 | 0.00 | 366.31 | 1686.42 | 4519.60 | 497493.19 | 3562.00 |
| 0.25 | 6140.99 | 11.10 | 125.00 | 16.67 | 44.46 | 0.00 | 524.62 | 2149.34 | 5760.24 | 634056.72 | 5718.00 |
| 0.5 | 6209.41 | 11.26 | 121.00 | 16.13 | 43.15 | 0.00 | 534.03 | 2173.29 | 5824.42 | 641121.25 | 5871.00 |
| 0.75 | 6364.94 | 11.51 | 121.00 | 16.13 | 42.31 | 0.00 | 548.63 | 2227.73 | 5970.31 | 657179.88 | 6009.00 |
| 1 | 6331.92 | 11.45 | 121.00 | 16.13 | 42.14 | 0.00 | 547.24 | 2216.17 | 5939.34 | 653770.45 | 6030.00 |
| 1.5 | 6386.81 | 11.55 | 121.00 | 16.13 | 41.86 | 0.00 | 552.75 | 2235.38 | 5990.83 | 659438.57 | 6074.00 |
| 2 | 6587.42 | 11.91 | 121.00 | 16.13 | 41.80 | 0.00 | 558.24 | 2305.60 | 6179.00 | 680150.89 | 6082.00 |

**Selected default: `YELLOW_TOLERANCE = 0`.** 0 is the largest tested tolerance below fixed on both total distance and overflow events.
