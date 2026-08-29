# 30-day predictive collection simulation

> **SIMULATED DATA.** This proves policy logic on a modeled Baikonur district; real savings must be measured during the pilot.

World: 250 sites in sector Өндіріс.

> **SECTOR SCOPE WARNING.** Sector-boundary validation failed: only 1 of 250 sites falls inside the Өндіріс OSM polygon. This frozen world must not be presented as a validated residential sector sample.

**Реальных площадок из OSM: 13 из 250; остальные размещены на реальных улицах.**

Area-type composition: commercial: 120 (48.0%), mixed: 108 (43.2%), private: 20 (8.0%), multistorey: 2 (0.8%).

The fixed baseline is a compliant, idealised calendar. A real operator also reacts to complaints, so actual current practice lies somewhere between fixed and predictive.

Distance source: road_cache.

Distance savings are never presented alone: overflow events and max-interval violations are shown in the same table.

| Policy | km_total | Δ vs fixed | overflow_events | max_interval_violations |
|---|---:|---:|---:|---:|
| fixed | 6079.52 | +0.0% | 195 | 0 |
| fixed_naive | 34381.71 | +465.5% | 195 | 0 |
| predictive | 5095.04 | -16.2% | 171 | 0 |
| predictive_reds_only | 5095.04 | -16.2% | 171 | 0 |

## All KPIs

| KPI | fixed | fixed_naive | predictive | predictive_reds_only |
|---|---:|---:|---:|---:|
| km_total | 6079.52 | 34381.71 | 5095.04 | 5095.04 |
| km_per_tonne | 11.04 | 62.42 | 9.43 | 9.43 |
| overflow_events | 195.00 | 195.00 | 171.00 | 171.00 |
| overflow_site_days | 26.00 | 26.00 | 22.80 | 22.80 |
| mean_fill_at_pickup | 46.16 | 46.16 | 70.30 | 70.30 |
| max_interval_violations | 0.00 | 0.00 | 0.00 | 0.00 |
| truck_hours | 510.82 | 1137.49 | 375.69 | 375.69 |
| fuel_liters | 2127.83 | 12033.60 | 1783.26 | 1783.26 |
| co2_kg | 5702.59 | 32250.05 | 4779.15 | 4779.15 |
| cost_kzt | 627710.19 | 3549912.07 | 526063.00 | 526063.00 |
| sites_served | 5480.00 | 5480.00 | 3562.00 | 3562.00 |

## Predictive delta vs fixed

| KPI | Delta |
|---|---:|
| km_total | -16.2% |
| km_per_tonne | -14.5% |
| overflow_events | -12.3% |
| overflow_site_days | -12.3% |
| mean_fill_at_pickup | +52.3% |
| max_interval_violations | +0.0% |
| truck_hours | -26.5% |
| fuel_liters | -16.2% |
| co2_kg | -16.2% |
| cost_kzt | -16.2% |
| sites_served | -35.0% |

**Yellow rule is inert at the selected default:** predictive and predictive_reds_only are identical on distance, overflow, and sites served.

## Marginal YELLOW detour-budget trade-off sweep

![Distance versus overflow frontier](yellow_detour_frontier.svg)

| detour_budget_m_per_m3 | km_total | km_per_tonne | overflow_events | overflow_site_days | mean_fill_at_pickup | max_interval_violations | truck_hours | fuel_liters | co2_kg | cost_kzt | sites_served |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 5095.04 | 9.43 | 171.00 | 22.80 | 70.30 | 0.00 | 375.69 | 1783.26 | 4779.15 | 526063.00 | 3562.00 |
| 100 | 7411.51 | 13.40 | 121.00 | 16.13 | 41.89 | 0.00 | 585.48 | 2594.03 | 6952.00 | 765238.58 | 6068.00 |
| 200 | 7460.88 | 13.49 | 121.00 | 16.13 | 41.80 | 0.00 | 589.17 | 2611.31 | 6998.30 | 770335.40 | 6083.00 |
| 400 | 7449.93 | 13.47 | 121.00 | 16.13 | 41.80 | 0.00 | 588.30 | 2607.48 | 6988.03 | 769205.25 | 6082.00 |
| 800 | 7488.32 | 13.54 | 122.00 | 16.27 | 41.93 | 0.00 | 588.29 | 2620.91 | 7024.05 | 773169.17 | 6062.00 |
| 1600 | 7636.83 | 13.81 | 121.00 | 16.13 | 41.77 | 0.00 | 596.29 | 2672.89 | 7163.35 | 788503.19 | 6087.00 |

**Selected default: `DETOUR_BUDGET_M_PER_M3 = 0`.** 0 m/m³ is the largest tested detour budget below fixed on both measures: 5095.0 km and 171 overflow events versus fixed at 6079.5 km and 195.
