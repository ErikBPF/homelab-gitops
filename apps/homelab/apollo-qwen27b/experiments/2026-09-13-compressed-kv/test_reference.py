"""CPU bindings for the first CKV-02/04 invariants, not full feature steps."""

import math
import random
import unittest

import reference


class PackingContract(unittest.TestCase):
    def test_known_wire_vectors(self):
        self.assertEqual(reference.pack([1, 2, 15], 4), bytes.fromhex("210f"))
        self.assertEqual(reference.pack([1, 2, 7], 3), bytes.fromhex("d101"))
        self.assertEqual(reference.unpack(bytes.fromhex("210f"), 4, 3), [1, 2, 15])
        self.assertEqual(reference.unpack(bytes.fromhex("d101"), 3, 3), [1, 2, 7])

    def test_all_widths_and_page_tails(self):
        rng = random.Random(7391)
        for bits in range(1, 17):
            for count in (0, 1, 2, 3, 7, 8, 9, 255, 256, 257):
                with self.subTest(bits=bits, count=count):
                    values = [rng.randrange(1 << bits) for _ in range(count)]
                    payload = reference.pack(values, bits)
                    self.assertEqual(len(payload), (count * bits + 7) // 8)
                    self.assertEqual(reference.unpack(payload, bits, count), values)

    def test_invalid_fields_and_widths(self):
        for bits in (0, 17, -1, 4.0, True):
            with self.subTest(bits=bits), self.assertRaises(ValueError):
                reference.pack([], bits)
            with self.subTest(bits=bits), self.assertRaises(ValueError):
                reference.unpack(b"", bits, 0)
        for value in (-1, 16, 1.0, True, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                reference.pack([value], 4)

    def test_malformed_payload_and_count(self):
        for payload, count in ((b"", 1), (b"\x01\x00", 1), (b"\xf1", 1),
                               (b"", -1), (b"", 0.0), (b"", True)):
            with self.subTest(payload=payload, count=count), self.assertRaises(ValueError):
                reference.unpack(payload, 4, count)


class SegmentContract(unittest.TestCase):
    left = (1.0,) + (0.0,) * 7
    right = (0.0, 1.0) + (0.0,) * 6

    def test_midpoint_endpoints_and_radius(self):
        midpoint = reference.reconstruct(self.left, self.right, 0.5)
        self.assertAlmostEqual(midpoint[0], 1 / math.sqrt(2), places=12)
        self.assertAlmostEqual(midpoint[1], 1 / math.sqrt(2), places=12)
        self.assertAlmostEqual(math.hypot(*midpoint), 1, places=12)
        self.assertEqual(reference.reconstruct(self.left, self.right, 0), self.left)
        self.assertEqual(reference.reconstruct(self.left, self.right, 1), self.right)
        self.assertEqual(reference.reconstruct(self.left, self.right, 0.5, 0), (0.0,) * 8)
        self.assertAlmostEqual(reference.score_lut(self.left, self.left, self.right, 0.5, 2),
                               math.sqrt(2), places=12)

    def test_lut_matches_independent_reconstruction_for_changed_queries(self):
        rng = random.Random(4827)
        for _ in range(128):
            endpoints = []
            for _ in range(2):
                vector = [rng.uniform(-1, 1) for _ in range(8)]
                endpoints.append(tuple(x / math.hypot(*vector) for x in vector))
            left, right = endpoints
            alpha, radius = rng.random(), rng.uniform(0, 3)
            blended = [(1 - alpha) * x + alpha * y for x, y in zip(left, right)]
            expected_vector = [radius * x / math.hypot(*blended) for x in blended]
            for got, expected in zip(reference.reconstruct(left, right, alpha, radius), expected_vector):
                self.assertAlmostEqual(got, expected, places=12)
            for _ in range(2):
                query = [rng.uniform(-2, 2) for _ in range(8)]
                expected = math.fsum(q * x for q, x in zip(query, expected_vector))
                self.assertTrue(math.isclose(reference.score_lut(query, left, right, alpha, radius),
                                             expected, rel_tol=1e-12, abs_tol=1e-12))

    def test_zero_radius_with_large_finite_query(self):
        endpoint = (1 / math.sqrt(8),) * 8
        try:
            score = reference.score_lut((1e308,) * 8, endpoint, endpoint, 0.5, 0)
        except OverflowError as error:
            self.fail(f"zero-radius score overflowed: {error}")
        self.assertEqual(score, 0.0)

    def test_invalid_segments(self):
        cases = [(self.left, tuple(-x for x in self.left), 0.5, 1),
                 ((0.0,) * 8, self.right, 0.5, 1),
                 ((2.0,) + (0.0,) * 7, self.right, 0.5, 1),
                 (self.left[:-1], self.right, 0.5, 1),
                 ((math.nan,) + (0.0,) * 7, self.right, 0.5, 1)]
        cases += [(self.left, self.right, alpha, 1) for alpha in (-0.1, 1.1, math.nan)]
        cases += [(self.left, self.right, 0.5, r) for r in (-1, math.inf)]
        for left, right, alpha, radius in cases:
            with self.subTest(left=left, alpha=alpha, radius=radius), self.assertRaises(ValueError):
                reference.reconstruct(left, right, alpha, radius)
            with self.subTest(left=left, alpha=alpha, radius=radius), self.assertRaises(ValueError):
                reference.score_lut(self.left, left, right, alpha, radius)
        for query in (self.left[:-1], (math.inf,) + (0.0,) * 7):
            with self.subTest(query=query), self.assertRaises(ValueError):
                reference.score_lut(query, self.left, self.right, 0.5)


if __name__ == "__main__":
    unittest.main()
