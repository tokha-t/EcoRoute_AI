from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.predict import MAX_INTERVAL_DAYS, _load_or_train_model, assign_priority

STYLES_PATH = Path(__file__).resolve().parents[1] / "assets" / "styles.css"


class PriorityAssignmentTest(unittest.TestCase):
    def test_priority_respects_high_threshold(self) -> None:
        df = pd.DataFrame({"predicted_fill_pct": [82, 88, 92, 96]})

        result = assign_priority(df, threshold=95)

        self.assertEqual(result["priority"].tolist(), ["Skip", "Skip", "Skip", "Critical"])

    def test_priority_respects_low_threshold(self) -> None:
        df = pd.DataFrame({"predicted_fill_pct": [49, 50, 79, 80, 90]})

        result = assign_priority(df, threshold=50)

        self.assertEqual(result["priority"].tolist(), ["Skip", "Medium", "Medium", "High", "Critical"])

    def test_selected_rows_are_must_serve_and_skipped_are_not(self) -> None:
        df = pd.DataFrame(
            {
                "predicted_fill_pct": [95, 85, 76, 50],
                "hours_since_collection": [10, 10, 10, 10],
            }
        )

        result = assign_priority(df, threshold=75)

        self.assertEqual(result["priority"].tolist(), ["Critical", "High", "Medium", "Skip"])
        self.assertEqual(result["must_serve"].tolist(), [True, True, True, False])


class MaxIntervalRuleTest(unittest.TestCase):
    def test_overdue_bin_is_critical_even_at_low_fill(self) -> None:
        df = pd.DataFrame(
            {
                "predicted_fill_pct": [10.0],
                "hours_since_collection": [4 * 24],
            }
        )

        result = assign_priority(df, threshold=75)

        self.assertEqual(result["priority"].tolist(), ["Critical"])
        self.assertEqual(result["must_serve"].tolist(), [True])

    def test_max_interval_boundary_is_inclusive(self) -> None:
        limit_hours = MAX_INTERVAL_DAYS * 24
        df = pd.DataFrame(
            {
                "predicted_fill_pct": [10.0, 10.0],
                "hours_since_collection": [limit_hours, limit_hours - 1],
            }
        )

        result = assign_priority(df, threshold=75)

        self.assertEqual(result["priority"].tolist(), ["Critical", "Skip"])
        self.assertEqual(result["must_serve"].tolist(), [True, False])


class StylesheetTest(unittest.TestCase):
    def test_styles_css_loads_without_exception(self) -> None:
        css = STYLES_PATH.read_text(encoding="utf-8")

        self.assertTrue(css.strip())
        self.assertNotIn("<style>", css)


class LoadOrTrainModelTest(unittest.TestCase):
    def test_retrains_once_with_warning_when_model_load_fails(self) -> None:
        # A stale/corrupt pickle should be recovered by retraining exactly once,
        # with a logged warning naming the cause — not a bare, silent except.
        sentinel = object()
        with (
            patch("src.predict.Path") as fake_path,
            patch("src.predict.train_and_save_model") as fake_train,
            patch(
                "src.predict.joblib.load",
                side_effect=[ValueError("corrupt pickle"), sentinel],
            ) as fake_load,
        ):
            fake_path.return_value.exists.return_value = True  # skip the initial train
            with self.assertLogs("src.predict", level="WARNING") as logs:
                model = _load_or_train_model()

        self.assertIs(model, sentinel)
        self.assertEqual(fake_train.call_count, 1)  # retrained at most once
        self.assertEqual(fake_load.call_count, 2)
        self.assertTrue(any("corrupt pickle" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
