"""Photo -> fill-level estimation (SPEC_V1 6.2).

Modules:
- dataset: ingest tooling for self-collected field photos + labels.csv
- estimator: estimate_fill(image_path) via Anthropic vision API (v0 backend)
- evaluate: accuracy / macro-F1 / confusion matrix with a by-site group split
"""

from src.photo_fill.estimator import estimate_fill

__all__ = ["estimate_fill"]
