# 30-day predictive collection simulation

> **SIMULATED DATA.** This proves policy logic on a modeled Baikonur district; real savings must be measured during the pilot.

World: 250 sites in sector Өндіріс; real OSM records: 13; street-synthesized: 237.

Distance source: road_cache.

Distance savings are never presented alone: overflow events and max-interval violations are shown in the same table.

| Policy | km_total | Δ vs fixed | overflow_events | max_interval_violations |
|---|---:|---:|---:|---:|
| fixed | 5565.06 | +0.0% | 237 | 50 |
| fixed_naive | 34211.54 | +514.8% | 237 | 50 |
| predictive | 4819.25 | -13.4% | 171 | 0 |
| predictive_reds_only | 4819.25 | -13.4% | 171 | 0 |

## All KPIs

| KPI | fixed | fixed_naive | predictive | predictive_reds_only |
|---|---:|---:|---:|---:|
| km_total | 5565.06 | 34211.54 | 4819.25 | 4819.25 |
| km_per_tonne | 10.04 | 61.70 | 8.92 | 8.92 |
| overflow_events | 237.00 | 237.00 | 171.00 | 171.00 |
| overflow_site_days | 31.60 | 31.60 | 22.80 | 22.80 |
| mean_fill_at_pickup | 46.53 | 46.53 | 70.30 | 70.30 |
| max_interval_violations | 50.00 | 50.00 | 0.00 | 0.00 |
| truck_hours | 498.62 | 1141.23 | 366.59 | 366.59 |
| fuel_liters | 1947.77 | 11974.04 | 1686.74 | 1686.74 |
| co2_kg | 5220.02 | 32090.42 | 4520.46 | 4520.46 |
| cost_kzt | 574592.25 | 3532341.45 | 497587.40 | 497587.40 |
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
| truck_hours | -26.5% |
| fuel_liters | -13.4% |
| co2_kg | -13.4% |
| cost_kzt | -13.4% |
| sites_served | -35.0% |

## YELLOW_TOLERANCE trade-off sweep

![Distance versus overflow frontier](yellow_tolerance_frontier.svg)

| tolerance | km_total | km_per_tonne | overflow_events | overflow_site_days | mean_fill_at_pickup | max_interval_violations | truck_hours | fuel_liters | co2_kg | cost_kzt | sites_served |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 4819.25 | 8.92 | 171.00 | 22.80 | 70.30 | 0.00 | 366.59 | 1686.74 | 4520.46 | 497587.40 | 3562.00 |
| 0.25 | 6081.66 | 11.00 | 127.00 | 16.93 | 45.16 | 0.00 | 553.19 | 2128.58 | 5704.60 | 627931.81 | 5628.00 |
| 0.5 | 6209.41 | 11.25 | 124.00 | 16.53 | 43.18 | 0.00 | 569.53 | 2173.29 | 5824.42 | 641121.35 | 5872.00 |
| 0.75 | 6417.74 | 11.60 | 121.00 | 16.13 | 42.25 | 0.00 | 592.66 | 2246.21 | 6019.84 | 662631.15 | 6017.00 |
| 1 | 6391.79 | 11.56 | 121.00 | 16.13 | 42.09 | 0.00 | 593.05 | 2237.13 | 5995.50 | 659952.28 | 6037.00 |
| 1.5 | 6444.33 | 11.65 | 121.00 | 16.13 | 41.86 | 0.00 | 590.62 | 2255.52 | 6044.78 | 665377.34 | 6073.00 |
| 2 | 6663.68 | 12.05 | 121.00 | 16.13 | 41.81 | 0.00 | 601.44 | 2332.29 | 6250.53 | 688024.95 | 6081.00 |

**Selected default: `YELLOW_TOLERANCE = 0`.** 0 is the largest tested tolerance below fixed on both total distance and overflow events.
