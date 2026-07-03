from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from src.photo_fill.estimator import (
    ANTHROPIC_VERSION,
    CONFIDENCE_THRESHOLD,
    FILL_CLASSES,
    MODEL,
    PCT_RANGES,
    UNCERTAIN,
    EstimationError,
    MalformedOutputError,
    api_key_available,
    estimate_fill,
)

FAKE_KEY_ENV = {"ANTHROPIC_API_KEY": "test-key-not-real"}


def _api_response(cls: str = "full", confidence: float = 0.92) -> dict:
    text = json.dumps({"cls": cls, "confidence": confidence, "visible_issues": []})
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakePost:
    """Stands in for requests.post, returning queued payloads and recording calls."""

    def __init__(self, payloads: list[dict]) -> None:
        self._payloads = list(payloads)
        self.calls: list[dict] = []

    def __call__(self, url: str, **kwargs) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return _FakeResponse(self._payloads.pop(0))


class EstimateFillTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.image = Path(self._tmp.name) / "photo.jpg"
        Image.new("RGB", (32, 24), (90, 90, 90)).save(self.image)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, payloads: list[dict]) -> tuple[dict, _FakePost]:
        fake_post = _FakePost(payloads)
        with patch.dict(os.environ, FAKE_KEY_ENV):
            with patch("src.photo_fill.estimator.requests.post", fake_post):
                result = estimate_fill(self.image)
        return result, fake_post

    def test_returns_contract_dict(self) -> None:
        result, _ = self._run([_api_response("full", 0.92)])
        self.assertEqual(result["cls"], "full")
        self.assertEqual(result["pct_range"], PCT_RANGES["full"])
        self.assertIsInstance(result["pct_range"], tuple)
        self.assertAlmostEqual(result["confidence"], 0.92)
        self.assertEqual(set(result), {"cls", "pct_range", "confidence"})

    def test_request_shape(self) -> None:
        _, fake_post = self._run([_api_response()])
        self.assertEqual(len(fake_post.calls), 1)
        call = fake_post.calls[0]
        self.assertEqual(call["headers"]["x-api-key"], FAKE_KEY_ENV["ANTHROPIC_API_KEY"])
        self.assertEqual(call["headers"]["anthropic-version"], ANTHROPIC_VERSION)
        body = call["json"]
        self.assertEqual(body["model"], MODEL)
        blocks = body["messages"][0]["content"]
        self.assertEqual(blocks[0]["type"], "image")
        self.assertEqual(blocks[0]["source"]["type"], "base64")
        schema = body["output_config"]["format"]["schema"]
        self.assertEqual(schema["properties"]["cls"]["enum"], list(FILL_CLASSES))

    def test_low_confidence_becomes_uncertain(self) -> None:
        result, _ = self._run([_api_response("full", CONFIDENCE_THRESHOLD - 0.05)])
        self.assertEqual(result["cls"], UNCERTAIN)
        self.assertEqual(result["pct_range"], PCT_RANGES[UNCERTAIN])

    def test_confidence_at_threshold_keeps_class(self) -> None:
        result, _ = self._run([_api_response("half", CONFIDENCE_THRESHOLD)])
        self.assertEqual(result["cls"], "half")

    def test_retries_once_on_malformed_output(self) -> None:
        malformed = {"content": [{"type": "text", "text": "not json {"}]}
        result, fake_post = self._run([malformed, _api_response("empty", 0.8)])
        self.assertEqual(result["cls"], "empty")
        self.assertEqual(len(fake_post.calls), 2)

    def test_raises_after_second_malformed_output(self) -> None:
        bad_cls = json.dumps({"cls": "brimming", "confidence": 0.9, "visible_issues": []})
        malformed = {"content": [{"type": "text", "text": bad_cls}]}
        fake_post = _FakePost([malformed, malformed])
        with patch.dict(os.environ, FAKE_KEY_ENV):
            with patch("src.photo_fill.estimator.requests.post", fake_post):
                with self.assertRaises(MalformedOutputError):
                    estimate_fill(self.image)
        self.assertEqual(len(fake_post.calls), 2)

    def test_empty_content_counts_as_malformed(self) -> None:
        refusal = {"content": [], "stop_reason": "refusal"}
        fake_post = _FakePost([refusal, refusal])
        with patch.dict(os.environ, FAKE_KEY_ENV):
            with patch("src.photo_fill.estimator.requests.post", fake_post):
                with self.assertRaises(MalformedOutputError):
                    estimate_fill(self.image)

    def test_missing_api_key_raises_before_network(self) -> None:
        env = {key: value for key, value in os.environ.items() if key != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with patch("src.photo_fill.estimator.requests.post", _refuse_network):
                with self.assertRaises(EstimationError):
                    estimate_fill(self.image)

    def test_missing_image_raises_before_network(self) -> None:
        with patch.dict(os.environ, FAKE_KEY_ENV):
            with patch("src.photo_fill.estimator.requests.post", _refuse_network):
                with self.assertRaises(EstimationError):
                    estimate_fill(Path(self._tmp.name) / "nope.jpg")


def _refuse_network(*args, **kwargs):
    raise AssertionError("network must not be hit on this path")


class ApiKeyAvailableTest(unittest.TestCase):
    def test_true_when_key_is_set(self) -> None:
        with patch.dict(os.environ, FAKE_KEY_ENV):
            self.assertTrue(api_key_available())

    def test_false_when_key_is_missing_or_empty(self) -> None:
        env = {key: value for key, value in os.environ.items() if key != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(api_key_available())
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            self.assertFalse(api_key_available())


if __name__ == "__main__":
    unittest.main()
