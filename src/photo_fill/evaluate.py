"""Evaluate the photo fill estimator with a by-site group split (SPEC_V1 6.2).

CLI:
    python -m src.photo_fill.evaluate [--labels data/photos/labels.csv]

Photos of one site are near-duplicates, so the split is BY SITE: no site_id
ever appears in both the train/tune pool and the held-out test pool. Only the
test pool is scored. Output is a markdown report (reports/photo_eval.md) with
accuracy, macro-F1, and a per-class confusion matrix. Predictions with
cls="uncertain" count as their own predicted class (never as correct).
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from sklearn.metrics import confusion_matrix, f1_score

from src.photo_fill.dataset import PHOTOS_DIR, labels_path
from src.photo_fill.estimator import FILL_CLASSES, UNCERTAIN, estimate_fill

REPORT_PATH = Path("reports/photo_eval.md")
TEST_FRACTION = 0.3
SEED = 42
PRED_CLASSES = (*FILL_CLASSES, UNCERTAIN)


@dataclass(frozen=True)
class EvalResult:
    accuracy: float
    macro_f1: float
    confusion: list[list[int]]  # rows: true FILL_CLASSES, cols: PRED_CLASSES
    n_test_photos: int
    train_sites: list[str] = field(default_factory=list)
    test_sites: list[str] = field(default_factory=list)
    uncertain_rate: float = 0.0


def split_by_site(
    site_ids: Sequence[str],
    test_fraction: float = TEST_FRACTION,
    seed: int = SEED,
) -> tuple[list[str], list[str]]:
    """Split unique sites into (train_sites, test_sites) with no overlap.

    Deterministic for a given seed. Both pools are non-empty whenever there
    are at least two sites.
    """
    unique = sorted(set(site_ids))
    if len(unique) < 2:
        raise ValueError(
            f"need at least 2 distinct sites for a by-site split, got {len(unique)}"
        )
    shuffled = unique[:]
    random.Random(seed).shuffle(shuffled)
    n_test = min(max(1, round(len(unique) * test_fraction)), len(unique) - 1)
    test_sites = sorted(shuffled[:n_test])
    train_sites = sorted(shuffled[n_test:])
    return train_sites, test_sites


def read_labels(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"no labeled photos in {path}")
    return rows


def evaluate(
    rows: Sequence[dict[str, str]],
    photos_dir: Path = PHOTOS_DIR,
    estimate_fn: Callable[[Path], dict] = estimate_fill,
    test_fraction: float = TEST_FRACTION,
    seed: int = SEED,
) -> EvalResult:
    """Score the estimator on the held-out test sites only."""
    train_sites, test_sites = split_by_site(
        [row["site_id"] for row in rows], test_fraction, seed
    )
    test_rows = [row for row in rows if row["site_id"] in test_sites]

    y_true: list[str] = []
    y_pred: list[str] = []
    for row in test_rows:
        photo = photos_dir / row["site_id"] / row["filename"]
        y_true.append(row["label"])
        y_pred.append(estimate_fn(photo)["cls"])

    correct = sum(1 for t, p in zip(y_true, y_pred) if t == p)
    matrix = confusion_matrix(y_true, y_pred, labels=list(PRED_CLASSES))
    return EvalResult(
        accuracy=correct / len(y_true),
        macro_f1=float(
            f1_score(y_true, y_pred, labels=list(FILL_CLASSES), average="macro", zero_division=0)
        ),
        confusion=[row[: len(PRED_CLASSES)].tolist() for row in matrix[: len(FILL_CLASSES)]],
        n_test_photos=len(y_true),
        train_sites=train_sites,
        test_sites=test_sites,
        uncertain_rate=y_pred.count(UNCERTAIN) / len(y_pred),
    )


def render_markdown(result: EvalResult, generated_at: datetime | None = None) -> str:
    generated_at = generated_at or datetime.now()
    header = "| true \\ predicted | " + " | ".join(PRED_CLASSES) + " |"
    divider = "|---" * (len(PRED_CLASSES) + 1) + "|"
    matrix_rows = [
        f"| **{true_cls}** | " + " | ".join(str(count) for count in row) + " |"
        for true_cls, row in zip(FILL_CLASSES, result.confusion)
    ]
    return "\n".join(
        [
            "# Photo Fill Estimator — Evaluation Report",
            "",
            f"Generated: {generated_at:%Y-%m-%d %H:%M} · backend: VLM v0 (zero-shot)",
            "",
            "Split is **by site** (SPEC_V1 6.2): no site appears in both the",
            "train/tune pool and the held-out test pool. Metrics below are on the",
            "test pool only. `uncertain` predictions are never counted as correct.",
            "",
            "## Split",
            "",
            f"- Train/tune sites ({len(result.train_sites)}): {', '.join(result.train_sites)}",
            f"- Test sites ({len(result.test_sites)}): {', '.join(result.test_sites)}",
            f"- Test photos: {result.n_test_photos}",
            "",
            "## Metrics",
            "",
            f"- **Accuracy:** {result.accuracy:.1%}",
            f"- **Macro-F1:** {result.macro_f1:.3f}",
            f"- **Uncertain rate:** {result.uncertain_rate:.1%} (routed to manual check)",
            "",
            "## Confusion matrix",
            "",
            header,
            divider,
            *matrix_rows,
            "",
            "Known limitation: winter/night photo degradation is not yet measured",
            "(SPEC_V1 6.2). P0 gate: accuracy >= 85% before the UI milestone.",
            "",
        ]
    )


def main(
    argv: Sequence[str] | None = None,
    estimate_fn: Callable[[Path], dict] = estimate_fill,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.photo_fill.evaluate",
        description="Evaluate the photo fill estimator on held-out sites.",
    )
    parser.add_argument("--labels", type=Path, default=labels_path())
    parser.add_argument("--photos-dir", type=Path, default=PHOTOS_DIR)
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    parser.add_argument("--test-fraction", type=float, default=TEST_FRACTION)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)

    try:
        rows = read_labels(args.labels)
        result = evaluate(
            rows,
            photos_dir=args.photos_dir,
            estimate_fn=estimate_fn,
            test_fraction=args.test_fraction,
            seed=args.seed,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_markdown(result), encoding="utf-8")
    print(
        f"accuracy={result.accuracy:.1%} macro_f1={result.macro_f1:.3f} "
        f"uncertain={result.uncertain_rate:.1%} on {result.n_test_photos} photos "
        f"from {len(result.test_sites)} held-out site(s)"
    )
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
