from __future__ import annotations

from pathlib import Path

from src.optimize.distances import DistanceMatrix
from src.sim.run import POLICIES, run_comparison, write_comparison_report
from src.sim.world import generate_world
from tests.test_sim_world import mock_osm_payload


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
    world = generate_world(seed=3, n_sites=30, payload=mock_osm_payload())
    matrix = matrix_for_world(world)
    monkeypatch.setattr("src.sim.run.get_matrix", lambda *args, **kwargs: matrix)
    results = run_comparison(world, days=4, seed=9)
    assert set(results) == set(POLICIES)
    assert all(len(records) == 4 for records in results.values())
    assert sum(record.max_interval_violations for record in results["predictive"]) == 0
    assert sum(record.overflow_events for record in results["predictive"]) <= sum(
        record.overflow_events for record in results["fixed"]
    )

    markdown = tmp_path / "simulation.md"
    csv = tmp_path / "simulation.csv"
    write_comparison_report(world, results, markdown, csv)
    text = markdown.read_text(encoding="utf-8")
    assert "km_total" in text
    assert "overflow_events" in text
    assert "max_interval_violations" in text
    assert "SIMULATED DATA" in text
    assert csv.exists()
