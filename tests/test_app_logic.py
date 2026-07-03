from __future__ import annotations

import unittest

import pandas as pd

from src.app_logic import (
    FILL_PCT_BY_CLASS,
    FLAG_AUTO,
    FLAG_MANUAL_CHECK,
    MODE_LIVE_PHOTO,
    MODE_SIMULATION,
    OBSERVATION_COLUMNS,
    PRIORITY_BY_CLASS,
    SIMULATED_BANNER_TEXT,
    confirmed_observations,
    guess_site_id,
    include_by_default,
    merge_observations,
    observation_rows,
    observations_to_plan_sites,
    show_simulated_banner,
)
from src.photo_fill.estimator import FILL_CLASSES, PCT_RANGES, UNCERTAIN


def observation(site_id: str, cls: str, photo: str = "p.jpg", confidence: float = 0.9,
                include: bool = True) -> dict:
    return {
        "photo": photo,
        "site_id": site_id,
        "cls": cls,
        "confidence": confidence,
        "flag": FLAG_AUTO,
        "include": include,
    }


def registry() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "bin_id": ["BIN-0001", "BIN-0002", "BIN-0003"],
            "latitude": [51.10, 51.11, 51.12],
            "longitude": [71.40, 71.41, 71.42],
            "district": ["Downtown", "Downtown", "North"],
            "waste_type": ["Paper", "Plastic", "Mixed"],
            "capacity_liters": [660, 1100, 660],
        }
    )


class TestSimulatedBannerFlag(unittest.TestCase):
    def test_simulation_mode_shows_banner(self):
        self.assertTrue(show_simulated_banner(MODE_SIMULATION))

    def test_live_photo_mode_hides_banner(self):
        self.assertFalse(show_simulated_banner(MODE_LIVE_PHOTO))

    def test_unknown_mode_fails_honest(self):
        self.assertTrue(show_simulated_banner("Something else"))

    def test_banner_text_says_simulated(self):
        self.assertIn("Simulated", SIMULATED_BANNER_TEXT)


class TestClassMappings(unittest.TestCase):
    def test_fill_pct_is_range_midpoint(self):
        for cls, (low, high) in PCT_RANGES.items():
            self.assertEqual(FILL_PCT_BY_CLASS[cls], (low + high) / 2)

    def test_every_class_has_a_priority(self):
        for cls in (*FILL_CLASSES, UNCERTAIN):
            self.assertIn(PRIORITY_BY_CLASS[cls], {"Critical", "High", "Medium"})

    def test_overflowing_is_critical(self):
        self.assertEqual(PRIORITY_BY_CLASS["overflowing"], "Critical")

    def test_uncertain_excluded_by_default(self):
        self.assertFalse(include_by_default(UNCERTAIN))
        for cls in FILL_CLASSES:
            self.assertTrue(include_by_default(cls))


class TestGuessSiteId(unittest.TestCase):
    def test_matches_id_in_filename_case_insensitive(self):
        self.assertEqual(guess_site_id("bin-0002_morning.JPG", ["BIN-0001", "BIN-0002"]), "BIN-0002")

    def test_no_match_returns_empty(self):
        self.assertEqual(guess_site_id("IMG_4432.jpg", ["BIN-0001"]), "")


class TestObservationRows(unittest.TestCase):
    def test_columns_and_defaults(self):
        rows = observation_rows(
            [
                {"photo": "BIN-0001.jpg", "cls": "full", "confidence": 0.91},
                {"photo": "dark.jpg", "cls": UNCERTAIN, "confidence": 0.31},
            ],
            ["BIN-0001"],
        )
        self.assertEqual(list(rows.columns), list(OBSERVATION_COLUMNS))
        confident, uncertain = rows.iloc[0], rows.iloc[1]
        self.assertEqual(confident["site_id"], "BIN-0001")
        self.assertEqual(confident["flag"], FLAG_AUTO)
        self.assertTrue(confident["include"])
        self.assertEqual(uncertain["site_id"], "")
        self.assertEqual(uncertain["flag"], FLAG_MANUAL_CHECK)
        self.assertFalse(uncertain["include"])

    def test_empty_input(self):
        rows = observation_rows([], ["BIN-0001"])
        self.assertTrue(rows.empty)
        self.assertEqual(list(rows.columns), list(OBSERVATION_COLUMNS))


class TestConfirmedObservations(unittest.TestCase):
    def test_filters_unchecked_siteless_and_uncertain(self):
        edited = pd.DataFrame(
            [
                observation("BIN-0001", "full"),
                observation("BIN-0002", "half", include=False),
                observation("", "full"),
                observation("BIN-0003", UNCERTAIN),  # ticked but unresolved
            ]
        )
        confirmed = confirmed_observations(edited)
        self.assertEqual(confirmed["site_id"].tolist(), ["BIN-0001"])

    def test_empty_frame_passes_through(self):
        edited = pd.DataFrame(columns=list(OBSERVATION_COLUMNS))
        self.assertTrue(confirmed_observations(edited).empty)


class TestMergeObservations(unittest.TestCase):
    def test_fuller_class_wins_on_conflict(self):
        existing = pd.DataFrame([observation("BIN-0001", "overflowing", photo="old.jpg")])
        new = pd.DataFrame([observation("BIN-0001", "half", photo="new.jpg")])
        merged = merge_observations(existing, new)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged.iloc[0]["cls"], "overflowing")

    def test_newer_wins_on_equal_class(self):
        existing = pd.DataFrame([observation("BIN-0001", "full", photo="old.jpg")])
        new = pd.DataFrame([observation("BIN-0001", "full", photo="new.jpg")])
        merged = merge_observations(existing, new)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged.iloc[0]["photo"], "new.jpg")

    def test_distinct_sites_are_kept(self):
        existing = pd.DataFrame([observation("BIN-0001", "full")])
        new = pd.DataFrame([observation("BIN-0002", "half")])
        merged = merge_observations(existing, new)
        self.assertEqual(merged["site_id"].tolist(), ["BIN-0001", "BIN-0002"])

    def test_none_existing_plan(self):
        new = pd.DataFrame([observation("BIN-0001", "full")])
        self.assertEqual(len(merge_observations(None, new)), 1)

    def test_both_empty(self):
        merged = merge_observations(None, pd.DataFrame(columns=list(OBSERVATION_COLUMNS)))
        self.assertTrue(merged.empty)
        self.assertEqual(list(merged.columns), list(OBSERVATION_COLUMNS))


class TestObservationsToPlanSites(unittest.TestCase):
    def test_join_sets_fill_priority_and_must_serve(self):
        observations = pd.DataFrame(
            [observation("BIN-0001", "overflowing"), observation("BIN-0003", "half")]
        )
        sites = observations_to_plan_sites(registry(), observations)
        self.assertEqual(sites["bin_id"].tolist(), ["BIN-0001", "BIN-0003"])
        self.assertTrue(sites["must_serve"].all())
        first = sites.iloc[0]
        self.assertEqual(first["fill_pct"], FILL_PCT_BY_CLASS["overflowing"])
        self.assertEqual(first["predicted_fill_pct"], FILL_PCT_BY_CLASS["overflowing"])
        self.assertEqual(first["priority"], "Critical")
        self.assertEqual(first["district"], "Downtown")
        self.assertEqual(first["photo_class"], "overflowing")
        self.assertEqual(sites.iloc[1]["priority"], "Medium")

    def test_duplicate_site_ids_are_merged_first(self):
        observations = pd.DataFrame(
            [observation("BIN-0001", "half"), observation("BIN-0001", "full")]
        )
        sites = observations_to_plan_sites(registry(), observations)
        self.assertEqual(len(sites), 1)
        self.assertEqual(sites.iloc[0]["fill_pct"], FILL_PCT_BY_CLASS["full"])

    def test_unknown_site_raises(self):
        observations = pd.DataFrame([observation("BIN-9999", "full")])
        with self.assertRaises(ValueError):
            observations_to_plan_sites(registry(), observations)

    def test_empty_observations_yield_empty_sites(self):
        sites = observations_to_plan_sites(
            registry(), pd.DataFrame(columns=list(OBSERVATION_COLUMNS))
        )
        self.assertTrue(sites.empty)


if __name__ == "__main__":
    unittest.main()
