"""Dependency-free Google encoded-polyline precision-5 codec."""

from __future__ import annotations

from collections.abc import Sequence

Point = tuple[float, float]


def _encode_value(value: int) -> str:
    value = ~(value << 1) if value < 0 else value << 1
    chars: list[str] = []
    while value >= 0x20:
        chars.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    chars.append(chr(value + 63))
    return "".join(chars)


def encode_polyline(points: Sequence[Point], precision: int = 5) -> str:
    """Encode ``(lat, lon)`` points using the Google polyline algorithm."""
    factor = 10**precision
    previous_lat = previous_lon = 0
    encoded: list[str] = []
    for lat, lon in points:
        current_lat = int(round(float(lat) * factor))
        current_lon = int(round(float(lon) * factor))
        encoded.append(_encode_value(current_lat - previous_lat))
        encoded.append(_encode_value(current_lon - previous_lon))
        previous_lat, previous_lon = current_lat, current_lon
    return "".join(encoded)


def decode_polyline(encoded: str, precision: int = 5) -> list[Point]:
    """Decode a polyline into ``(lat, lon)`` points."""
    factor = float(10**precision)
    index = latitude = longitude = 0
    points: list[Point] = []
    while index < len(encoded):
        deltas: list[int] = []
        for _ in range(2):
            result = shift = 0
            while True:
                if index >= len(encoded):
                    raise ValueError("truncated encoded polyline")
                value = ord(encoded[index]) - 63
                index += 1
                result |= (value & 0x1F) << shift
                shift += 5
                if value < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        latitude += deltas[0]
        longitude += deltas[1]
        points.append((latitude / factor, longitude / factor))
    return points
