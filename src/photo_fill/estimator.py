"""Photo -> fill-level estimation, v0 backend: Anthropic vision API (SPEC_V1 6.2).

Contract:
    estimate_fill(image_path) -> {"cls": str, "pct_range": tuple, "confidence": float}

`cls` is one of FILL_CLASSES, or "uncertain" when the model's confidence is
below CONFIDENCE_THRESHOLD — uncertain photos go to the manual-check list,
never silently guessed. The API key is read from the ANTHROPIC_API_KEY
environment variable; it is never hardcoded or logged.

Uses requests directly (project rule: no anthropic SDK dependency). The
response format is constrained server-side via output_config json_schema and
still parsed strictly, with one retry on malformed output.
"""

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path

import requests
from PIL import Image, ImageOps

from src.config import CONFIDENCE_THRESHOLD, VLM_MODEL

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL = VLM_MODEL
MAX_TOKENS = 512
REQUEST_TIMEOUT_SECONDS = 30.0
MAX_IMAGE_EDGE = 1568  # downscale phone photos: fewer tokens, same class signal
JPEG_QUALITY = 90

# Upload guards (SPEC_V1 6.2 hardening). The extension allowlist and size cap run
# in the app's photo path before any temp file is written; the pixel cap makes PIL
# raise Image.DecompressionBombError instead of decoding a decompression bomb, and
# estimate_fill() turns that into an EstimationError (table-safe error row).
Image.MAX_IMAGE_PIXELS = 40_000_000
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
ALLOWED_UPLOAD_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})

FILL_CLASSES = ("empty", "half", "full", "overflowing")
UNCERTAIN = "uncertain"
PCT_RANGES: dict[str, tuple[int, int]] = {
    "empty": (0, 25),
    "half": (25, 60),
    "full": (60, 90),
    "overflowing": (90, 100),
    UNCERTAIN: (0, 100),
}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "cls": {"type": "string", "enum": list(FILL_CLASSES)},
        "confidence": {"type": "number"},
        "visible_issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["cls", "confidence", "visible_issues"],
    "additionalProperties": False,
}

PROMPT = (
    "You are assessing a photo of a municipal waste collection site (container/bin). "
    "Classify the overall fill level of the container(s) in view:\n"
    "- empty: little to no waste visible inside\n"
    "- half: partially filled, clearly below the rim\n"
    "- full: filled to around the rim, lid may not close\n"
    "- overflowing: waste above the rim or spilling around the container\n"
    "Report confidence as a number between 0 and 1. If the container interior is "
    "not clearly visible (angle, distance, darkness, obstruction), lower your "
    "confidence accordingly. List visible operational issues (scattered waste, "
    "damaged container, blocked access, snow cover) in visible_issues, or an "
    "empty list if none."
)


class EstimationError(RuntimeError):
    """The estimator could not produce a usable result."""


class MalformedOutputError(EstimationError):
    """The API responded, but not with parseable, schema-valid JSON."""


def api_key_available() -> bool:
    """True when ANTHROPIC_API_KEY is set — the UI gates live photo mode on this."""
    return bool(os.environ.get("ANTHROPIC_API_KEY", ""))


def upload_rejection_reason(filename: str, size_bytes: int) -> str | None:
    """Why an uploaded photo must be rejected, or None if it passes the guards.

    Enforces the extension allowlist (case-insensitive) and MAX_UPLOAD_BYTES cap.
    Both checks are cheap and side-effect free so the app can run them before
    writing any temp file and turn the message into a table-safe error row instead
    of raising (SPEC_V1 6.2 hardening).
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_UPLOAD_EXTENSIONS))
        return f"unsupported file type '{suffix or filename}' (allowed: {allowed})"
    if size_bytes > MAX_UPLOAD_BYTES:
        max_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        return f"file too large ({size_bytes / (1024 * 1024):.1f} MB, max {max_mb} MB)"
    return None


def _api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise EstimationError(
            "ANTHROPIC_API_KEY is not set; export it before calling estimate_fill()"
        )
    return key


def _encode_image(image_path: Path) -> tuple[str, str]:
    """Return (base64_data, media_type), downscaling oversized photos to JPEG."""
    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img)
        if max(img.size) > MAX_IMAGE_EDGE:
            img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
            buffer = io.BytesIO()
            img.convert("RGB").save(buffer, "JPEG", quality=JPEG_QUALITY)
            return base64.standard_b64encode(buffer.getvalue()).decode("ascii"), "image/jpeg"

    media_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    return base64.standard_b64encode(image_path.read_bytes()).decode("ascii"), media_type


def _build_payload(image_data: str, media_type: str, model: str) -> dict:
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
        "output_config": {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    }


def _parse_response(payload: dict) -> tuple[str, float]:
    """Strictly extract (cls, confidence) or raise MalformedOutputError."""
    content = payload.get("content") or []
    text = next(
        (block.get("text") for block in content if block.get("type") == "text"), None
    )
    if not text:
        raise MalformedOutputError(
            f"no text block in response (stop_reason={payload.get('stop_reason')!r})"
        )
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise MalformedOutputError(f"response is not valid JSON: {text[:200]}") from exc

    cls = data.get("cls") if isinstance(data, dict) else None
    if cls not in FILL_CLASSES:
        raise MalformedOutputError(f"cls missing or not in {FILL_CLASSES}: {cls!r}")
    try:
        confidence = float(data["confidence"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MalformedOutputError("confidence missing or not a number") from exc
    return cls, min(max(confidence, 0.0), 1.0)


def _call_api(payload: dict, api_key: str, timeout: float) -> dict:
    response = requests.post(
        ANTHROPIC_API_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def estimate_fill(
    image_path: str | Path,
    *,
    model: str = MODEL,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> dict:
    """Estimate the fill level of the waste container shown in the photo.

    Returns {"cls": str, "pct_range": (low, high), "confidence": float}.
    Retries once when the API returns malformed output; confidence below
    CONFIDENCE_THRESHOLD yields cls="uncertain" (manual-check path).
    """
    image_path = Path(image_path)
    if not image_path.is_file():
        raise EstimationError(f"image not found: {image_path}")

    api_key = _api_key()
    try:
        image_data, media_type = _encode_image(image_path)
    except Image.DecompressionBombError as exc:
        raise EstimationError(
            f"image rejected by decompression-bomb guard: {image_path.name}"
        ) from exc
    payload = _build_payload(image_data, media_type, model)

    last_error: MalformedOutputError | None = None
    for _ in range(2):  # one retry on malformed output
        try:
            cls, confidence = _parse_response(_call_api(payload, api_key, timeout))
            break
        except MalformedOutputError as exc:
            last_error = exc
    else:
        raise MalformedOutputError(
            f"malformed output after retry for {image_path.name}: {last_error}"
        )

    if confidence < CONFIDENCE_THRESHOLD:
        cls = UNCERTAIN
    return {"cls": cls, "pct_range": PCT_RANGES[cls], "confidence": confidence}
