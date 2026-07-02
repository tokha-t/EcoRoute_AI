from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from src.predict import MAX_INTERVAL_DAYS, assign_priority


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


if __name__ == "__main__":
    unittest.main()
