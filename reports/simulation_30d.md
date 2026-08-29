# 30-day predictive collection simulation

> **SIMULATED DATA.** This proves policy logic on a modeled Baikonur district; real savings must be measured during the pilot.

World: 250 sites in sector Жастар.

**Sector boundary validated: 250 of 250 sites (100.0%) are inside the Жастар polygon.**

**Реальных площадок из OSM: 5 из 250; остальные размещены на реальных улицах.**

Area-type composition: commercial: 114 (45.6%), mixed: 88 (35.2%), private: 30 (12.0%), multistorey: 18 (7.2%).

The fixed baseline is a compliant, idealised calendar. A real operator also reacts to complaints, so actual current practice lies somewhere between fixed and predictive.

Distance source: road_cache.

Distance savings are never presented alone: overflow events and max-interval violations are shown in the same table.

| Policy | km_total | Δ vs fixed | overflow_events | max_interval_violations |
|---|---:|---:|---:|---:|
| fixed | 3870.55 | +0.0% | 324 | 0 |
| fixed_naive | 20149.18 | +420.6% | 324 | 0 |
| predictive | 3462.89 | -10.5% | 214 | 0 |
| predictive_reds_only | 3462.89 | -10.5% | 214 | 0 |

## All KPIs

| KPI | fixed | fixed_naive | predictive | predictive_reds_only |
|---|---:|---:|---:|---:|
| km_total | 3870.55 | 20149.18 | 3462.89 | 3462.89 |
| km_per_tonne | 6.93 | 36.10 | 6.31 | 6.31 |
| overflow_events | 324.00 | 324.00 | 214.00 | 214.00 |
| overflow_site_days | 43.20 | 43.20 | 28.53 | 28.53 |
| mean_fill_at_pickup | 46.45 | 46.45 | 69.59 | 69.59 |
| max_interval_violations | 0.00 | 0.00 | 0.00 | 0.00 |
| truck_hours | 441.01 | 847.57 | 329.68 | 329.68 |
| fuel_liters | 1354.69 | 7052.21 | 1212.01 | 1212.01 |
| co2_kg | 3630.58 | 18899.93 | 3248.19 | 3248.19 |
| cost_kzt | 399634.39 | 2080402.34 | 357543.76 | 357543.76 |
| sites_served | 5580.00 | 5580.00 | 3692.00 | 3692.00 |

## Predictive delta vs fixed

| KPI | Delta |
|---|---:|
| km_total | -10.5% |
| km_per_tonne | -8.9% |
| overflow_events | -34.0% |
| overflow_site_days | -34.0% |
| mean_fill_at_pickup | +49.8% |
| max_interval_violations | +0.0% |
| truck_hours | -25.2% |
| fuel_liters | -10.5% |
| co2_kg | -10.5% |
| cost_kzt | -10.5% |
| sites_served | -33.8% |

**Yellow rule is inert at the selected default:** predictive and predictive_reds_only are identical on distance, overflow, and sites served.

## Marginal YELLOW detour-budget trade-off sweep

![Distance versus overflow frontier](yellow_detour_frontier.svg)

| detour_budget_m_per_m3 | km_total | km_per_tonne | overflow_events | overflow_site_days | mean_fill_at_pickup | max_interval_violations | truck_hours | fuel_liters | co2_kg | cost_kzt | sites_served |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 3462.89 | 6.31 | 214.00 | 28.53 | 69.59 | 0.00 | 329.68 | 1212.01 | 3248.19 | 357543.76 | 3692.00 |
| 100 | 4578.65 | 8.16 | 170.00 | 22.67 | 43.24 | 0.00 | 493.61 | 1602.53 | 4294.78 | 472746.03 | 6022.00 |
| 200 | 4579.22 | 8.16 | 170.00 | 22.67 | 43.24 | 0.00 | 493.60 | 1602.73 | 4295.31 | 472804.43 | 6022.00 |
| 400 | 4544.51 | 8.10 | 170.00 | 22.67 | 43.24 | 0.00 | 491.87 | 1590.58 | 4262.75 | 469220.98 | 6022.00 |
| 800 | 4599.02 | 8.19 | 170.00 | 22.67 | 43.24 | 0.00 | 494.11 | 1609.66 | 4313.88 | 474849.26 | 6022.00 |
| 1600 | 4780.71 | 8.52 | 170.00 | 22.67 | 43.24 | 0.00 | 501.11 | 1673.25 | 4484.31 | 493608.37 | 6022.00 |

**Selected default: `DETOUR_BUDGET_M_PER_M3 = 0`.** 0 m/m³ is the largest tested detour budget below fixed on both measures: 3462.9 km and 214 overflow events versus fixed at 3870.6 km and 324.
