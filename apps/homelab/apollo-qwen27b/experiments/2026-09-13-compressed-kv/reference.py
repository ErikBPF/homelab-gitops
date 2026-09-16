"""Portable compressed-KV mathematical reference; no model or GPU measurements."""

import json
import math
import random


def pack(values, bits):
    if type(bits) is not int or not 1 <= bits <= 16:
        raise ValueError("bits must be an integer from 1 through 16")
    payload = bytearray()
    accumulator = used = 0
    for value in values:
        if type(value) is not int or not 0 <= value < 1 << bits:
            raise ValueError("field must be an unsigned integer fitting bits")
        accumulator |= value << used
        used += bits
        while used >= 8:
            payload.append(accumulator & 255)
            accumulator >>= 8
            used -= 8
    if used:
        payload.append(accumulator)
    return bytes(payload)


def unpack(payload, bits, count):
    if type(bits) is not int or not 1 <= bits <= 16:
        raise ValueError("bits must be an integer from 1 through 16")
    if type(count) is not int or count < 0:
        raise ValueError("count must be a nonnegative integer")
    if not isinstance(payload, (bytes, bytearray)) or len(payload) != (count * bits + 7) // 8:
        raise ValueError("payload must contain exactly ceil(count * bits / 8) bytes")
    trailing = count * bits % 8
    if trailing and payload[-1] >> trailing:
        raise ValueError("unused trailing bits must be zero")
    values = []
    accumulator = used = 0
    for byte in payload:
        accumulator |= byte << used
        used += 8
        while used >= bits and len(values) < count:
            values.append(accumulator & ((1 << bits) - 1))
            accumulator >>= bits
            used -= bits
    return values


def _segment(left, right, alpha, radius):
    try:
        left, right = tuple(left), tuple(right)
        for endpoint in (left, right):
            if (len(endpoint) != 8 or not all(math.isfinite(x) for x in endpoint)
                    or abs(math.hypot(*endpoint) - 1) > 1e-6):
                raise ValueError("endpoints must have eight finite unit-direction components")
        if not math.isfinite(alpha) or not 0 <= alpha <= 1:
            raise ValueError("alpha must be finite and between zero and one")
        if not math.isfinite(radius) or radius < 0:
            raise ValueError("radius must be finite and nonnegative")
        blended = tuple((1 - alpha) * x + alpha * y for x, y in zip(left, right))
        norm = math.hypot(*blended)
        if norm <= 1e-8:
            raise ValueError("interpolation norm must exceed 1e-8")
    except (TypeError, OverflowError) as error:
        raise ValueError("invalid segment input") from error
    return left, right, blended, norm


def reconstruct(left, right, alpha, radius=1.0):
    _, _, blended, norm = _segment(left, right, alpha, radius)
    return tuple(radius * (x / norm) for x in blended)


def score_lut(query, left, right, alpha, radius=1.0):
    left, right, _, norm = _segment(left, right, alpha, radius)
    try:
        query = tuple(query)
        if len(query) != 8 or not all(math.isfinite(x) for x in query):
            raise ValueError("query must have eight finite components")
    except (TypeError, OverflowError) as error:
        raise ValueError("invalid query") from error
    if radius == 0:
        return 0.0
    left_score = math.fsum(q * x for q, x in zip(query, left))
    right_score = math.fsum(q * x for q, x in zip(query, right))
    return radius * (((1 - alpha) * left_score + alpha * right_score) / norm)


if __name__ == "__main__":
    seed = 4827
    rng = random.Random(seed)
    field_cases = packed_bytes = 0
    for bits in range(1, 17):
        for count in (0, 1, 2, 3, 7, 8, 9, 255, 256, 257):
            values = [rng.randrange(1 << bits) for _ in range(count)]
            payload = pack(values, bits)
            if len(payload) != (count * bits + 7) // 8 or unpack(payload, bits, count) != values:
                raise AssertionError("packed fields did not round-trip")
            field_cases += 1
            packed_bytes += len(payload)
    segment_cases = 128
    max_score_error = 0.0
    for _ in range(segment_cases):
        endpoints = []
        for _ in range(2):
            vector = [rng.uniform(-1, 1) for _ in range(8)]
            endpoints.append(tuple(x / math.hypot(*vector) for x in vector))
        left, right = endpoints
        alpha, radius = rng.random(), rng.uniform(0, 3)
        blended = [(1 - alpha) * x + alpha * y for x, y in zip(left, right)]
        expected = [radius * x / math.hypot(*blended) for x in blended]
        if not all(math.isclose(x, y, rel_tol=1e-12, abs_tol=1e-12)
                   for x, y in zip(reconstruct(left, right, alpha, radius), expected)):
            raise AssertionError("reconstruction disagrees with normalized interpolation")
        for _ in range(2):
            query = [rng.uniform(-2, 2) for _ in range(8)]
            score = score_lut(query, left, right, alpha, radius)
            expected_score = math.fsum(q * x for q, x in zip(query, expected))
            max_score_error = max(max_score_error, abs(score - expected_score))
            if not math.isclose(score, expected_score, rel_tol=1e-12, abs_tol=1e-12):
                raise AssertionError("LUT score disagrees with reconstructed-vector score")
    print(json.dumps({"seed": seed, "field_cases": field_cases,
                      "segment_cases": segment_cases, "score_cases": segment_cases * 2,
                      "packed_bytes": packed_bytes, "max_score_error": max_score_error,
                      "scope": "synthetic_reference_only"}, sort_keys=True))
