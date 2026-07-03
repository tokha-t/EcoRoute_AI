from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from src.photo_fill.dataset import (
    LABELS_COLUMNS,
    VALID_LABELS,
    DatasetError,
    add_photos,
    labels_path,
    main,
    validate_label,
)


def _make_image(path: Path, color: tuple[int, int, int] = (120, 120, 120)) -> Path:
    Image.new("RGB", (32, 24), color).save(path)
    return path


def _read_labels(photos_dir: Path) -> list[dict[str, str]]:
    with labels_path(photos_dir).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class LabelValidationTest(unittest.TestCase):
    def test_accepts_all_valid_labels(self) -> None:
        for label in VALID_LABELS:
            self.assertEqual(validate_label(label), label)

    def test_rejects_unknown_label(self) -> None:
        with self.assertRaises(DatasetError):
            validate_label("overflow")  # close but not a valid class


class AddPhotosRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.inbox = root / "inbox"
        self.inbox.mkdir()
        self.photos_dir = root / "photos"
        _make_image(self.inbox / "a.jpg")
        _make_image(self.inbox / "b.png", color=(30, 200, 30))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_stores_photos_and_appends_labels(self) -> None:
        stored = add_photos(self.inbox, "S001", "full", photos_dir=self.photos_dir)

        self.assertEqual(len(stored), 2)
        for path in stored:
            self.assertTrue(path.is_file())
            self.assertEqual(path.parent, self.photos_dir / "S001")
            self.assertEqual(path.suffix, ".jpg")
            with Image.open(path) as img:  # stored files stay readable images
                img.verify()

        rows = _read_labels(self.photos_dir)
        self.assertEqual(len(rows), 2)
        for row, path in zip(rows, stored):
            self.assertEqual(tuple(row.keys()), LABELS_COLUMNS)
            self.assertEqual(row["site_id"], "S001")
            self.assertEqual(row["filename"], path.name)
            self.assertEqual(row["label"], "full")
            self.assertEqual(row["labeler"], "founder")

    def test_second_add_appends_without_second_header(self) -> None:
        add_photos(self.inbox, "S001", "full", photos_dir=self.photos_dir)
        add_photos(self.inbox, "S002", "empty", labeler="friend", photos_dir=self.photos_dir)

        rows = _read_labels(self.photos_dir)
        self.assertEqual(len(rows), 4)
        self.assertEqual({row["site_id"] for row in rows}, {"S001", "S002"})
        self.assertEqual(rows[-1]["labeler"], "friend")
        # identical timestamps across adds must not overwrite files
        self.assertEqual(len({row["filename"] for row in rows if row["site_id"] == "S001"}), 2)

    def test_rejects_invalid_label_before_storing(self) -> None:
        with self.assertRaises(DatasetError):
            add_photos(self.inbox, "S001", "brimming", photos_dir=self.photos_dir)
        self.assertFalse(labels_path(self.photos_dir).exists())

    def test_rejects_empty_folder(self) -> None:
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        with self.assertRaises(DatasetError):
            add_photos(empty, "S001", "full", photos_dir=self.photos_dir)

    def test_rejects_non_image_bytes(self) -> None:
        bad = Path(self._tmp.name) / "bad"
        bad.mkdir()
        (bad / "fake.jpg").write_text("not an image")
        with self.assertRaises(DatasetError):
            add_photos(bad, "S001", "full", photos_dir=self.photos_dir)


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.inbox = root / "inbox"
        self.inbox.mkdir()
        self.photos_dir = root / "photos"
        _make_image(self.inbox / "a.jpg")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_add_command_round_trip(self) -> None:
        exit_code = main(
            [
                "add",
                str(self.inbox),
                "--site-id",
                "S010",
                "--label",
                "half",
                "--photos-dir",
                str(self.photos_dir),
            ]
        )
        self.assertEqual(exit_code, 0)
        rows = _read_labels(self.photos_dir)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["site_id"], "S010")
        self.assertEqual(rows[0]["label"], "half")

    def test_cli_rejects_invalid_label(self) -> None:
        with self.assertRaises(SystemExit):  # argparse choices enforcement
            main(["add", str(self.inbox), "--site-id", "S1", "--label", "nope"])

    def test_missing_folder_returns_error_code(self) -> None:
        exit_code = main(
            [
                "add",
                str(Path(self._tmp.name) / "missing"),
                "--site-id",
                "S1",
                "--label",
                "full",
                "--photos-dir",
                str(self.photos_dir),
            ]
        )
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
