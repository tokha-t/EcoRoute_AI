"""Ingest tooling for self-collected field photos (SPEC_V1 6.2).

CLI:
    python -m src.photo_fill.dataset add <folder> --site-id S001 --label full

Photos are stored as data/photos/{site_id}/{timestamp}.jpg and every stored
photo appends one row to data/photos/labels.csv. Real photos never get
committed (data/photos/ is gitignored); labels.csv is the committed artifact.
"""

from __future__ import annotations

import argparse
import csv
import shutil
from datetime import datetime
from pathlib import Path
from typing import Sequence

from PIL import Image, UnidentifiedImageError

PHOTOS_DIR = Path("data/photos")
LABELS_FILENAME = "labels.csv"
LABELS_COLUMNS = ("site_id", "filename", "ts", "label", "labeler")
VALID_LABELS = ("empty", "half", "full", "overflowing")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")
JPEG_QUALITY = 92
TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S"


class DatasetError(ValueError):
    """Invalid ingest input (bad label, unreadable image, empty folder)."""


def labels_path(photos_dir: Path = PHOTOS_DIR) -> Path:
    return photos_dir / LABELS_FILENAME


def validate_label(label: str) -> str:
    if label not in VALID_LABELS:
        raise DatasetError(f"label must be one of {VALID_LABELS}, got {label!r}")
    return label


def _capture_timestamp(source: Path) -> str:
    """Capture time approximated by file mtime (phones preserve it on transfer)."""
    return datetime.fromtimestamp(source.stat().st_mtime).strftime(TIMESTAMP_FORMAT)


def _unique_destination(site_dir: Path, ts: str) -> Path:
    dest = site_dir / f"{ts}.jpg"
    counter = 1
    while dest.exists():
        dest = site_dir / f"{ts}_{counter}.jpg"
        counter += 1
    return dest


def _store_as_jpeg(source: Path, dest: Path) -> None:
    """Validate the image and store it as JPEG (jpg copied as-is, png converted)."""
    try:
        with Image.open(source) as img:
            img.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise DatasetError(f"not a readable image: {source}") from exc

    if source.suffix.lower() in (".jpg", ".jpeg"):
        shutil.copyfile(source, dest)
        return
    with Image.open(source) as img:
        img.convert("RGB").save(dest, "JPEG", quality=JPEG_QUALITY)


def _append_label_row(photos_dir: Path, row: dict[str, str]) -> None:
    path = labels_path(photos_dir)
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LABELS_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def add_photos(
    folder: Path,
    site_id: str,
    label: str,
    labeler: str = "founder",
    photos_dir: Path = PHOTOS_DIR,
) -> list[Path]:
    """Ingest every image in `folder` for one site with one label.

    Returns the list of stored photo paths. All photos in one folder share the
    site_id and label — the field capture protocol is one folder per site walk.
    """
    validate_label(label)
    folder = Path(folder)
    if not folder.is_dir():
        raise DatasetError(f"not a folder: {folder}")

    sources = sorted(
        entry for entry in folder.iterdir() if entry.suffix.lower() in IMAGE_SUFFIXES
    )
    if not sources:
        raise DatasetError(f"no images ({', '.join(IMAGE_SUFFIXES)}) found in {folder}")

    site_dir = photos_dir / site_id
    site_dir.mkdir(parents=True, exist_ok=True)

    stored: list[Path] = []
    for source in sources:
        ts = _capture_timestamp(source)
        dest = _unique_destination(site_dir, ts)
        _store_as_jpeg(source, dest)
        _append_label_row(
            photos_dir,
            {
                "site_id": site_id,
                "filename": dest.name,
                "ts": ts,
                "label": label,
                "labeler": labeler,
            },
        )
        stored.append(dest)
    return stored


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.photo_fill.dataset",
        description="Ingest field photos into the labeled photo dataset.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="add all images in a folder for one site")
    add.add_argument("folder", type=Path, help="folder containing photos of one site")
    add.add_argument("--site-id", required=True, help="site identifier, e.g. S001")
    add.add_argument("--label", required=True, choices=VALID_LABELS)
    add.add_argument("--labeler", default="founder", help="who assigned the label")
    add.add_argument(
        "--photos-dir", type=Path, default=PHOTOS_DIR, help="dataset root (default: data/photos)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        stored = add_photos(
            folder=args.folder,
            site_id=args.site_id,
            label=args.label,
            labeler=args.labeler,
            photos_dir=args.photos_dir,
        )
    except DatasetError as exc:
        print(f"error: {exc}")
        return 1
    print(f"stored {len(stored)} photo(s) for site {args.site_id} (label={args.label}):")
    for path in stored:
        print(f"  {path}")
    print(f"labels file: {labels_path(args.photos_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
