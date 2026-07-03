from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.photo_fill.estimator import PCT_RANGES, UNCERTAIN
from src.photo_fill.evaluate import (
    FILL_CLASSES,
    PRED_CLASSES,
    evaluate,
    main,
    render_markdown,
    split_by_site,
)


def _labels(n_sites: int = 10, photos_per_site: int = 3) -> list[dict[str, str]]:
    rows = []
    for site_index in range(n_sites):
        site_id = f"S{site_index:03d}"
        for photo_index in range(photos_per_site):
            rows.append(
                {
                    "site_id": site_id,
                    "filename": f"20260701T08000{photo_index}.jpg",
                    "ts": f"20260701T08000{photo_index}",
                    "label": FILL_CLASSES[(site_index + photo_index) % len(FILL_CLASSES)],
                    "labeler": "founder",
                }
            )
    return rows


def _perfect_estimator(rows: list[dict[str, str]]):
    """Fake estimate_fn that answers with the ground-truth label for each path."""
    by_path = {f"{row['site_id']}/{row['filename']}": row["label"] for row in rows}

    def estimate(photo: Path) -> dict:
        cls = by_path[f"{photo.parent.name}/{photo.name}"]
        return {"cls": cls, "pct_range": PCT_RANGES[cls], "confidence": 0.95}

    return estimate


def _always_uncertain(photo: Path) -> dict:
    return {"cls": UNCERTAIN, "pct_range": PCT_RANGES[UNCERTAIN], "confidence": 0.3}


class SplitBySiteTest(unittest.TestCase):
    def test_no_site_in_both_pools(self) -> None:
        sites = [f"S{i:03d}" for i in range(10)]
        train, test = split_by_site(sites, test_fraction=0.3, seed=42)
        self.assertEqual(set(train) & set(test), set())
        self.assertEqual(sorted(train + test), sites)
        self.assertEqual(len(test), 3)

    def test_deterministic_for_same_seed(self) -> None:
        sites = [f"S{i:03d}" for i in range(20)]
        self.assertEqual(split_by_site(sites, seed=7), split_by_site(sites, seed=7))

    def test_duplicate_rows_do_not_leak_between_pools(self) -> None:
        # multiple photos per site: split operates on unique site ids
        sites = ["S001", "S001", "S002", "S002", "S003", "S003"]
        train, test = split_by_site(sites, test_fraction=0.34, seed=1)
        self.assertEqual(set(train) & set(test), set())
        self.assertTrue(train and test)

    def test_both_pools_nonempty_even_at_extreme_fractions(self) -> None:
        sites = ["S001", "S002"]
        for fraction in (0.01, 0.99):
            train, test = split_by_site(sites, test_fraction=fraction, seed=3)
            self.assertEqual(len(train), 1)
            self.assertEqual(len(test), 1)

    def test_single_site_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            split_by_site(["S001", "S001"])


class EvaluateTest(unittest.TestCase):
    def test_perfect_estimator_scores_one(self) -> None:
        rows = _labels()
        result = evaluate(rows, estimate_fn=_perfect_estimator(rows))
        self.assertEqual(result.accuracy, 1.0)
        self.assertEqual(result.macro_f1, 1.0)
        self.assertEqual(result.uncertain_rate, 0.0)
        # off-diagonal (within the 4 true classes) must be empty
        for i, row in enumerate(result.confusion):
            for j, count in enumerate(row[: len(FILL_CLASSES)]):
                if i != j:
                    self.assertEqual(count, 0)

    def test_only_test_site_photos_are_scored(self) -> None:
        rows = _labels(n_sites=10, photos_per_site=3)
        scored_paths: list[Path] = []

        def recording_estimator(photo: Path) -> dict:
            scored_paths.append(photo)
            return _always_uncertain(photo)

        result = evaluate(rows, estimate_fn=recording_estimator)
        scored_sites = {path.parent.name for path in scored_paths}
        self.assertEqual(scored_sites, set(result.test_sites))
        self.assertEqual(len(scored_paths), len(result.test_sites) * 3)
        self.assertEqual(set(result.train_sites) & scored_sites, set())

    def test_uncertain_predictions_never_count_as_correct(self) -> None:
        rows = _labels()
        result = evaluate(rows, estimate_fn=_always_uncertain)
        self.assertEqual(result.accuracy, 0.0)
        self.assertEqual(result.macro_f1, 0.0)
        self.assertEqual(result.uncertain_rate, 1.0)
        uncertain_col = PRED_CLASSES.index(UNCERTAIN)
        total_uncertain = sum(row[uncertain_col] for row in result.confusion)
        self.assertEqual(total_uncertain, result.n_test_photos)


class ReportTest(unittest.TestCase):
    def test_markdown_contains_metrics_and_matrix(self) -> None:
        rows = _labels()
        result = evaluate(rows, estimate_fn=_perfect_estimator(rows))
        report = render_markdown(result)
        self.assertIn("Accuracy:** 100.0%", report)
        self.assertIn("Macro-F1:** 1.000", report)
        self.assertIn("| true \\ predicted |", report)
        for cls in FILL_CLASSES:
            self.assertIn(f"| **{cls}** |", report)
        self.assertIn(UNCERTAIN, report)

    def test_main_writes_report_file(self) -> None:
        rows = _labels()
        with tempfile.TemporaryDirectory() as tmp:
            labels_csv = Path(tmp) / "labels.csv"
            import csv

            with labels_csv.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)

            out = Path(tmp) / "reports" / "photo_eval.md"
            exit_code = main(
                ["--labels", str(labels_csv), "--out", str(out)],
                estimate_fn=_perfect_estimator(rows),
            )
            self.assertEqual(exit_code, 0)
            self.assertIn("Accuracy", out.read_text(encoding="utf-8"))

    def test_main_reports_error_on_missing_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exit_code = main(
                ["--labels", str(Path(tmp) / "missing.csv")],
                estimate_fn=_always_uncertain,
            )
            self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
