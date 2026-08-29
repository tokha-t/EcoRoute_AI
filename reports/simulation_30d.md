# 30-day predictive collection simulation

> **SIMULATED DATA.** This proves policy logic on a modeled Baikonur district; real savings must be measured during the pilot.

World: 250 sites in sector Жастар.

**Sector boundary validated: 250 of 250 sites (100.0%) are inside the Жастар polygon.**

**Реальных площадок из OSM: 5 из 250; остальные размещены на реальных улицах.**

Area-type composition: mixed: 194 (77.6%), multistorey: 49 (19.6%), private: 7 (2.8%).

The fixed baseline is a compliant, idealised calendar. A real operator also reacts to complaints, so actual current practice lies somewhere between fixed and predictive.

Distance source: road_cache.

Distance savings are never presented alone: overflow events and max-interval violations are shown in the same table.

| Policy | km_total | Δ vs fixed | overflow_events | max_interval_violations |
|---|---:|---:|---:|---:|
| fixed | 3628.97 | +0.0% | 399 | 0 |
| fixed_naive | 15440.78 | +325.5% | 399 | 0 |
| predictive | 3039.09 | -16.3% | 153 | 0 |
| predictive_reds_only | 3039.09 | -16.3% | 153 | 0 |

## All KPIs

| KPI | fixed | fixed_naive | predictive | predictive_reds_only |
|---|---:|---:|---:|---:|
| km_total | 3628.97 | 15440.78 | 3039.09 | 3039.09 |
| km_per_tonne | 7.48 | 31.83 | 6.34 | 6.34 |
| overflow_events | 399.00 | 399.00 | 153.00 | 153.00 |
| overflow_site_days | 53.20 | 53.20 | 20.40 | 20.40 |
| mean_fill_at_pickup | 51.57 | 51.57 | 67.92 | 67.92 |
| max_interval_violations | 0.00 | 0.00 | 0.00 | 0.00 |
| truck_hours | 375.11 | 675.53 | 294.98 | 294.98 |
| fuel_liters | 1270.14 | 5404.27 | 1063.68 | 1063.68 |
| co2_kg | 3403.97 | 14483.45 | 2850.67 | 2850.67 |
| cost_kzt | 374690.72 | 1594260.28 | 313786.31 | 313786.31 |
| sites_served | 4450.00 | 4450.00 | 3353.00 | 3353.00 |

## Predictive delta vs fixed

| KPI | Delta |
|---|---:|
| km_total | -16.3% |
| km_per_tonne | -15.3% |
| overflow_events | -61.7% |
| overflow_site_days | -61.7% |
| mean_fill_at_pickup | +31.7% |
| max_interval_violations | +0.0% |
| truck_hours | -21.4% |
| fuel_liters | -16.3% |
| co2_kg | -16.3% |
| cost_kzt | -16.3% |
| sites_served | -24.7% |

**Yellow rule is inert at the selected default:** predictive and predictive_reds_only are identical on distance, overflow, and sites served. **Жёлтые баки на текущей настройке не собираются.**

## Marginal YELLOW detour-budget trade-off sweep

![Distance versus overflow frontier](yellow_detour_frontier.svg)

| detour_budget_m_per_m3 | km_total | km_per_tonne | overflow_events | overflow_site_days | mean_fill_at_pickup | max_interval_violations | truck_hours | fuel_liters | co2_kg | cost_kzt | sites_served |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 3039.09 | 6.34 | 153.00 | 20.40 | 67.92 | 0.00 | 294.98 | 1063.68 | 2850.67 | 313786.31 | 3353.00 |
| 5 | 3789.69 | 7.82 | 136.00 | 18.13 | 57.49 | 0.00 | 356.36 | 1326.39 | 3554.73 | 391285.56 | 3984.00 |
| 10 | 3871.35 | 7.98 | 142.00 | 18.93 | 57.28 | 0.00 | 360.75 | 1354.97 | 3631.33 | 399717.31 | 4001.00 |
| 20 | 3844.38 | 7.92 | 138.00 | 18.40 | 57.36 | 0.00 | 358.61 | 1345.53 | 3606.02 | 396931.74 | 3992.00 |
| 30 | 3919.43 | 8.07 | 143.00 | 19.07 | 56.65 | 0.00 | 365.07 | 1371.80 | 3676.43 | 404681.13 | 4048.00 |
| 50 | 3815.25 | 7.87 | 145.00 | 19.33 | 56.01 | 0.00 | 363.06 | 1335.34 | 3578.71 | 393924.88 | 4090.00 |
| 75 | 3790.18 | 7.79 | 143.00 | 19.07 | 55.02 | 0.00 | 366.70 | 1326.56 | 3555.18 | 391335.65 | 4171.00 |
| 100 | 3735.34 | 7.64 | 144.00 | 19.20 | 53.82 | 0.00 | 369.64 | 1307.37 | 3503.75 | 385674.06 | 4277.00 |
| 400 | 3947.51 | 8.04 | 135.00 | 18.00 | 47.03 | 0.00 | 410.62 | 1381.63 | 3702.77 | 407580.50 | 4913.00 |
| 1600 | 4252.20 | 8.67 | 124.00 | 16.53 | 41.55 | 0.00 | 457.31 | 1488.27 | 3988.57 | 439040.10 | 5566.00 |

**Selected default: `DETOUR_BUDGET_M_PER_M3 = 0`.** 0 m/m³ is the largest tested detour budget below fixed on both measures: 3039.1 km and 153 overflow events versus fixed at 3629.0 km and 399.

## Fleet adequacy

**No tested operating point reaches zero overflow.** The best tested configuration records 124 overflow events (16.53 per 1000 site-days) at 1600 m/m³. **Текущий парк недостаточен для нулевого переполнения при смоделированных ограничениях смены и маршрутизации.**
