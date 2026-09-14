"""Lock logarithmic grid fields, matched scalar bytes and frozen-Q mechanics."""

import math
import os
import sys
import unittest

os.environ['USE_TF'] = '0'
os.environ['USE_FLAX'] = '0'
sys.path.append('/host-deps')

import torch
from scalar_probe import scalar_roundtrip
import log_probe


class LogTests(unittest.TestCase):
    def test_signed_wire_zero_groups_and_ties_toward_zero(self):
        vectors = torch.tensor([[-7., 7., -7 / 256, 7 / 256] * 8, [0.] * 32])
        result = log_probe.log_roundtrip(vectors, 4)
        self.assertIsNotNone(result)
        restored, packed, scales = result
        self.assertEqual(bytes(packed.tolist()), bytes.fromhex('791f' * 8 + '00' * 16))
        self.assertTrue(torch.equal(restored, vectors))
        self.assertTrue(torch.equal(scales.flatten(), torch.ones(2)))
        boundary = torch.zeros(32)
        boundary[:4] = torch.tensor([-7., 7., 7 / 512, -7 / 512])
        boundary[4] = torch.nextafter(torch.tensor(7 / 512), torch.tensor(float('inf')))
        boundary[5] = -boundary[4]
        restored, packed, _ = log_probe.log_roundtrip(boundary, 4)
        self.assertEqual(bytes(packed.tolist()), bytes.fromhex('7900f1' + '00' * 13))
        self.assertTrue(torch.equal(restored[2:4], torch.zeros(2)))
        self.assertTrue(torch.equal(restored[4:6], torch.tensor([7 / 256, -7 / 256])))

    def test_all_widths_share_linear_scale_storage_and_bit2_values(self):
        vectors = torch.linspace(-3, 3, 256).reshape(2, 1, 128)
        saved = vectors.clone()
        for bits in (2, 4, 8):
            with self.subTest(bits=bits):
                result = log_probe.log_roundtrip(vectors, bits)
                self.assertIsNotNone(result)
                restored, packed, scales = result
                linear = scalar_roundtrip(vectors, bits)
                self.assertEqual(restored.dtype, torch.float32)
                self.assertEqual(restored.shape, vectors.shape)
                self.assertEqual(packed.dtype, torch.uint8)
                self.assertEqual(packed.numel(), vectors.numel() * bits // 8)
                self.assertEqual(scales.dtype, torch.float32)
                self.assertEqual(scales.numel() * scales.element_size(), 32)
                self.assertTrue(torch.equal(scales, linear[2]))
                self.assertTrue(torch.isfinite(restored).all())
                self.assertTrue(torch.equal(vectors, saved))
                if bits == 2:
                    self.assertTrue(all(torch.equal(actual, expected) for actual, expected in zip(result, linear)))

    def test_invalid_inputs_and_scale_underflow_are_explicit(self):
        valid = torch.zeros(32)
        for tensor, bits in ((valid, 3), (valid, True), (valid, 4.0),
                             (torch.zeros(31), 4), (torch.zeros(0, 32), 4),
                             (torch.ones(32, dtype=torch.bfloat16), 4),
                             (torch.ones(32, dtype=torch.float64), 4),
                             (torch.ones(32, device='meta'), 4),
                             (torch.full((32,), float('nan')), 4),
                             (torch.full((32,), float('inf')), 4),
                             (torch.full((32,), 1e-45), 8)):
            with self.subTest(bits=bits, shape=tuple(tensor.shape), dtype=tensor.dtype, device=tensor.device):
                with self.assertRaises(ValueError):
                    log_probe.log_roundtrip(tensor, bits)

    def test_comparison_matches_manual_attention_and_unpadded_bytes(self):
        generator = torch.Generator().manual_seed(4827)
        capture = {'q': torch.randn(1, 2, 1, 32, generator=generator),
                   'k': torch.randn(1, 1, 3, 32, generator=generator),
                   'v': torch.randn(1, 1, 3, 32, generator=generator), 'scale': 32 ** -0.5}
        saved = {side: capture[side].clone() for side in ('q', 'k', 'v')}

        def decode(tensor, bits, logarithmic):
            if bits == 'bf16':
                return tensor.bfloat16().float()
            maximum = 2 ** (bits - 1) - 1
            groups = tensor.reshape(-1, 32)
            scales = groups.abs().amax(dim=1, keepdim=True) / maximum
            if not logarithmic:
                return ((groups / scales).round().clamp(-maximum, maximum) * scales).reshape(tensor.shape)
            positive = torch.arange(1, maximum + 1, dtype=torch.float32)
            levels = (maximum * torch.exp2(-8 * (maximum - positive) / (maximum - 1))
                      if maximum > 1 else positive)
            levels = torch.cat((torch.zeros(1), levels))
            physical = scales[:, :, None] * levels[None, None, :]
            assigned = (groups.abs()[:, :, None] - physical).abs().argmin(dim=-1)
            return (groups.sign() * scales * levels[assigned]).reshape(tensor.shape)

        def attention(k, v):
            expanded_k, expanded_v = k.repeat_interleave(2, dim=1), v.repeat_interleave(2, dim=1)
            weights = ((capture['q'] @ expanded_k.transpose(-2, -1)) * capture['scale']).softmax(dim=-1)
            return weights @ expanded_v

        reference = attention(capture['k'], capture['v'])
        for bits, logarithmic in [(b, log) for b in (2, 4, 8) for log in (False, True)] + [('bf16', False)]:
            for mode in ('k', 'v', 'kv'):
                with self.subTest(bits=bits, logarithmic=logarithmic, mode=mode):
                    result = log_probe.comparison([capture], bits, mode, logarithmic)
                    self.assertIsNotNone(result)
                    decoded = {side: decode(capture[side], bits, logarithmic) if side in mode else capture[side]
                               for side in ('k', 'v')}
                    perturbed = attention(decoded['k'], decoded['v'])
                    for name, original, actual in [('k', capture['k'], decoded['k']),
                                                    ('v', capture['v'], decoded['v']),
                                                    ('attention', reference, perturbed)]:
                        expected = (original.double() - actual.double()).square().sum() / original.double().square().sum()
                        self.assertTrue(math.isfinite(result[name + '_nmse']))
                        self.assertAlmostEqual(result[name + '_nmse'], expected.item(), places=6)
                    sides = len(mode)
                    payload = sides * (192 if bits == 'bf16' else 96 * bits // 8)
                    scales = 0 if bits == 'bf16' else sides * 12
                    untouched = (2 - sides) * 384
                    self.assertEqual(result['payload_bytes'], payload)
                    self.assertEqual(result['scale_bytes'], scales)
                    self.assertEqual(result['untouched_fp32_bytes'], untouched)
                    self.assertEqual(result['raw_bytes'], payload + scales + untouched)
                    self.assertEqual(result['baseline_fp32_bytes'], 768)
                    self.assertEqual(result['table_bytes'], 0)
                    self.assertEqual(result['table_padding_bytes'], 0)
        self.assertTrue(all(torch.equal(capture[side], saved[side]) for side in saved))


if __name__ == '__main__':
    unittest.main()
