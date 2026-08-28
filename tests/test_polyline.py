from __future__ import annotations

import pytest

from src.geo.polyline import decode_polyline, encode_polyline


def test_polyline_precision_five_round_trip_and_known_vector() -> None:
    points = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    encoded = encode_polyline(points)
    assert encoded == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    assert decode_polyline(encoded) == pytest.approx(points, abs=1e-5)


def test_polyline_rejects_truncated_value() -> None:
    with pytest.raises(ValueError, match="truncated"):
        decode_polyline("_")
