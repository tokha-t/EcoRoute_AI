from __future__ import annotations

from pathlib import Path

from src.optimize.distances import DistanceMatrix
from src.sim.run import (
    DETOUR_BUDGET_SWEEP,
    POLICIES,
    assess_fleet_adequacy,
    run_comparison,
    run_full_analysis,
    select_detour_budget,
    summarize,
    write_comparison_report,
)
from src.sim.world import generate_world
from tests.test_sim_world import mock_boundary, mock_osm_payload


def matrix_for_world(world) -> DistanceMatrix:
    points = [(51.1735, 71.4010)] + list(zip(world["lat"], world["lon"])) + [(51.116, 71.357)]
    meters = [[0.0] * len(points) for _ in points]
    seconds = [[0.0] * len(points) for _ in points]
    for i, a in enumerate(points):
        for j, b in enumerate(points):
            distance = (((a[0] - b[0]) * 111_000) ** 2 + ((a[1] - b[1]) * 70_000) ** 2) ** 0.5
            meters[i][j] = distance
            seconds[i][j] = distance / 7
    return DistanceMatrix(seconds, meters, False, "test")


def test_short_comparison_and_report_safety_kpis(tmp_path: Path, monkeypatch) -> None:
    world = generate_world(
        seed=3,
        n_sites=30,
        payload=mock_osm_payload(),
        boundary=mock_boundary(),
    )
    matrix = matrix_for_world(world)
    monkeypatch.setattr("src.sim.run.get_matrix", lambda *args, **kwargs: matrix)
    results = run_comparison(world, days=4, seed=9)
    assert set(results) == set(POLICIES)
    assert all(len(records) == 4 for records in results.values())
    assert sum(record.max_interval_violations for record in results["predictive"]) == 0
    assert sum(record.max_interval_violations for record in results["fixed"]) == 0
    assert sum(record.overflow_events for record in results["predictive"]) <= sum(
        record.overflow_events for record in results["fixed"]
    )

    markdown = tmp_path / "simulation.md"
    csv = tmp_path / "simulation.csv"
    write_comparison_report(
        world,
        results,
        markdown,
        csv,
        road_cache_meta={
            "osrm_profile": "refuse_truck",
            "courtyard_access": {
                "threshold_m": 40.0,
                "sites_without_mapped_access": 3,
                "share_pct": 10.0,
            },
        },
    )
    text = markdown.read_text(encoding="utf-8")
    assert "km_total" in text
    assert "overflow_events" in text
    assert "max_interval_violations" in text
    assert "SIMULATED DATA" in text
    assert "compliant, idealised calendar" in text
    assert "Реальных площадок из OSM:" in text
    assert "Area-type composition:" in text
    assert "Жёлтые баки на текущей настройке не собираются" in text
    assert "Routing profile: **refuse_truck**" in text
    assert "площадок без картированного подъезда: 3 (10.0%)" in text
    assert csv.exists()


def test_detour_budget_sweep_report_and_selection_are_reproducible(
    tmp_path: Path, monkeypatch
) -> None:
    world = generate_world(
        seed=4,
        n_sites=18,
        payload=mock_osm_payload(),
        boundary=mock_boundary(),
    )
    matrix = matrix_for_world(world)
    monkeypatch.setattr("src.sim.run.get_matrix", lambda *args, **kwargs: matrix)
    results, sweep, selection = run_full_analysis(world, days=2, seed=11)
    assert tuple(sweep) == DETOUR_BUDGET_SWEEP
    assert selection == select_detour_budget(sweep, summarize(results["fixed"], len(world)))
    markdown = tmp_path / "simulation.md"
    csv = tmp_path / "simulation.csv"
    sweep_csv = tmp_path / "sweep.csv"
    sweep_svg = tmp_path / "frontier.svg"
    write_comparison_report(
        world,
        results,
        markdown,
        csv,
        sweep_summaries=sweep,
        selection=selection,
        sweep_csv_path=sweep_csv,
        sweep_svg_path=sweep_svg,
    )
    text = markdown.read_text(encoding="utf-8")
    assert "Marginal YELLOW detour-budget trade-off sweep" in text
    assert "Selected default" in text
    assert selection.reason in text
    assert "Fleet adequacy" in text
    assert sweep_csv.exists()
    assert sweep_svg.exists()


def test_fleet_adequacy_requires_a_zero_overflow_operating_point() -> None:
    inadequate = assess_fleet_adequacy(
        {
            0.0: {"overflow_events": 12.0, "overflow_site_days": 8.0, "km_total": 100.0},
            20.0: {"overflow_events": 4.0, "overflow_site_days": 2.5, "km_total": 130.0},
        }
    )
    adequate = assess_fleet_adequacy(
        {
            0.0: {"overflow_events": 1.0, "overflow_site_days": 0.5, "km_total": 100.0},
            20.0: {"overflow_events": 0.0, "overflow_site_days": 0.0, "km_total": 130.0},
        }
    )
    assert inadequate.adequate_for_zero_overflow is False
    assert inadequate.best_budget_m_per_m3 == 20.0
    assert adequate.adequate_for_zero_overflow is True
