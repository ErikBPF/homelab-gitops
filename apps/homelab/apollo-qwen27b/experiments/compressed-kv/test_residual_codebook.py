"""Lock residual wire bytes, weighted centroids and the actual GQA Jacobian."""

import math
import os
import sys
import unittest

os.environ['USE_TF'] = '0'
os.environ['USE_FLAX'] = '0'
sys.path.append('/host-deps')

import torch
import residual_codebook as codec


class ResidualCodebookTests(unittest.TestCase):
    def test_weighted_centroid_matches_independent_oracle(self):
        samples = torch.stack((torch.zeros(8), torch.full((8,), 10.)))
        initial = torch.full((1, 8), -2.)
        saved = samples.clone(), initial.clone()
        weighted = codec.fit_centers(samples, torch.tensor([1., 3.]), initial)
        self.assertIsNotNone(weighted)
        uniform = codec.fit_centers(samples, torch.ones(2), initial)
        self.assertIsNotNone(uniform)
        self.assertTrue(torch.equal(weighted, torch.full((1, 8), 7.5)))
        self.assertTrue(torch.equal(uniform, torch.full((1, 8), 5.)))
        self.assertFalse(torch.equal(weighted, uniform))
        self.assertTrue(torch.equal(samples, saved[0]))
        self.assertTrue(torch.equal(initial, saved[1]))

    def test_weight_floor_and_zero_fallback(self):
        raw = torch.tensor([0., 1., 3.])
        result = codec.normalize_weights(raw)
        self.assertIsNotNone(result)
        weights, fallback = result
        expected = torch.tensor([0.0002, 1., 3.])
        expected /= expected.mean()
        self.assertFalse(fallback)
        self.assertTrue(torch.allclose(weights, expected, atol=1e-7, rtol=1e-6))
        self.assertTrue(torch.equal(raw, torch.tensor([0., 1., 3.])))
        self.assertAlmostEqual(weights.mean().item(), 1., places=6)
        zero = codec.normalize_weights(torch.zeros(4))
        self.assertIsNotNone(zero)
        self.assertTrue(zero[1])
        self.assertTrue(torch.equal(zero[0], torch.ones(4)))

    def test_stored_indices_reconstruct_each_stage_and_charge_tables(self):
        for stages in (4, 5):
            with self.subTest(stages=stages):
                tables = torch.zeros(stages, 256, 8)
                tables[0, 1] = 2.
                tables[1, 2] = 0.5
                tables[2, 3] = 0.125
                tables[3, 4] = 0.03125
                if stages == 5:
                    tables[4, 5] = 0.0078125
                value = 2.65625 + (0.0078125 if stages == 5 else 0.)
                original = torch.full((1, 2, 3, 16), value)
                saved = original.clone(), tables.clone()
                result = codec.roundtrip(original, tables)
                self.assertIsNotNone(result)
                decoded, payload, scales = result
                self.assertEqual(tuple(payload.shape), (12, stages))
                self.assertEqual(payload.dtype, torch.uint8)
                self.assertEqual(payload.numel() * payload.element_size(), 12 * stages)
                self.assertEqual(tables.numel() * tables.element_size(), stages * 8192)
                self.assertEqual(scales.numel(), 0)
                self.assertEqual(decoded.shape, original.shape)
                self.assertEqual(decoded.device, original.device)
                self.assertTrue(torch.equal(payload, torch.arange(1, stages + 1).to(torch.uint8).expand(12, -1)))
                oracle = sum(tables[s, payload[:, s].long()] for s in range(stages))
                self.assertTrue(torch.equal(decoded.reshape(-1, 8), oracle))
                self.assertTrue(torch.equal(decoded, original))
                self.assertTrue(torch.equal(original, saved[0]))
                self.assertTrue(torch.equal(tables, saved[1]))

    def test_fit_is_seeded_finite_and_retains_both_stage_budgets(self):
        samples = torch.randn(270, 8, generator=torch.Generator().manual_seed(4827))
        saved = samples.clone()
        for stages in (4, 5):
            with self.subTest(stages=stages):
                first = codec.fit_residual(samples, 4827, stages)
                self.assertIsNotNone(first)
                second = codec.fit_residual(samples, 4827, stages, torch.ones(len(samples)))
                self.assertIsNotNone(second)
                self.assertEqual(tuple(first['tables'].shape), (stages, 256, 8))
                self.assertEqual(first['tables'].dtype, torch.float32)
                self.assertFalse(first['tables'].requires_grad)
                self.assertTrue(torch.equal(first['tables'], second['tables']))
                self.assertEqual(first['losses'], second['losses'])
                self.assertTrue(first['losses'])
                self.assertTrue(all(math.isfinite(loss) and loss >= 0 for loss in first['losses']))
                result = codec.roundtrip(samples, first['tables'])
                self.assertIsNotNone(result)
                actual_loss = (samples.double() - result[0].double()).square().mean().item()
                self.assertAlmostEqual(first['losses'][-1], actual_loss, places=7)
        self.assertTrue(torch.equal(samples, saved))

    def test_gqa_sensitivity_matches_full_autograd_jacobian(self):
        generator = torch.Generator().manual_seed(4828)
        q = torch.randn(1, 2, 1, 8, generator=generator)
        k = torch.randn(1, 1, 2, 8, generator=generator)
        v = torch.randn(1, 1, 2, 8, generator=generator)
        scale = 0.37
        saved = q.clone(), k.clone(), v.clone()

        def attend(keys, values):
            repeated_keys = keys.repeat_interleave(2, dim=1)
            repeated_values = values.repeat_interleave(2, dim=1)
            p = torch.softmax(q.double() @ repeated_keys.transpose(-1, -2) * scale, dim=-1)
            return p @ repeated_values

        key_jac = torch.autograd.functional.jacobian(lambda x: attend(x, v.double()), k.double())
        value_jac = torch.autograd.functional.jacobian(lambda x: attend(k.double(), x), v.double())
        key_expected = key_jac.reshape(16, -1).square().sum(0).reshape(k.shape)
        value_expected = value_jac.reshape(16, -1).square().sum(0).reshape(v.shape)
        result = codec.attention_sensitivity(q, k, v, scale)
        self.assertIsNotNone(result)
        for actual, expected, original in zip(result, (key_expected, value_expected), (k, v)):
            self.assertEqual(actual.shape, original.shape)
            self.assertEqual(actual.dtype, torch.float32)
            self.assertEqual(actual.device, original.device)
            self.assertTrue(torch.allclose(actual.double(), expected, atol=2e-7, rtol=2e-5))
            self.assertTrue((actual >= 0).all())
        self.assertTrue(all(torch.equal(a, b) for a, b in zip((q, k, v), saved)))

    def test_zero_query_has_zero_key_sensitivity_and_uniform_fallback(self):
        q, k, v = torch.zeros(1, 2, 1, 8), torch.ones(1, 1, 2, 8), torch.ones(1, 1, 2, 8)
        result = codec.attention_sensitivity(q, k, v, 0.5)
        self.assertIsNotNone(result)
        self.assertTrue(torch.equal(result[0], torch.zeros_like(k)))
        self.assertTrue(torch.equal(result[1], torch.full_like(v, 0.5)))
        normalized = codec.normalize_weights(result[0].reshape(-1, 8).mean(dim=1))
        self.assertIsNotNone(normalized)
        self.assertTrue(normalized[1])
        self.assertTrue(torch.equal(normalized[0], torch.ones(2)))

    def test_invalid_shapes_weights_and_nonfinite_arithmetic_are_rejected(self):
        good = torch.ones(256, 8)
        for weights in (torch.tensor([-1., 1.]), torch.tensor([float('nan')]),
                        torch.tensor([float('inf')]), torch.ones(2, 2), torch.empty(0)):
            with self.subTest(weights=weights), self.assertRaises(ValueError):
                codec.normalize_weights(weights)
        for samples, weights, initial in ((good, torch.ones(255), good[:1]),
                                         (good, -torch.ones(256), good[:1]),
                                         (good * float('inf'), torch.ones(256), good[:1]),
                                         (good, torch.ones(256), good[:1] * float('nan'))):
            with self.subTest(surface='fit_centers'), self.assertRaises(ValueError):
                codec.fit_centers(samples, weights, initial)
        for stages in (0, 3, 6, True):
            with self.subTest(stages=stages), self.assertRaises(ValueError):
                codec.fit_residual(good, 4827, stages)
        tables = torch.zeros(4, 256, 8)
        for tensor, table in ((torch.ones(1, 9), tables), (good * float('nan'), tables),
                              (good, tables[:, :255]), (good, tables + float('inf')),
                              (good * 3e20, tables - 3e20)):
            with self.subTest(surface='roundtrip'), self.assertRaises(ValueError):
                codec.roundtrip(tensor, table)
        q, k, v = torch.ones(1, 2, 1, 8), torch.ones(1, 1, 2, 8), torch.ones(1, 1, 2, 8)
        for query, keys, values, scale in ((q, k, v, float('nan')), (q, k, v, float('inf')),
                                          (q * float('nan'), k, v, 1.),
                                          (q[:, :, :, :7], k, v, 1.),
                                          (q, k, v[:, :, :1], 1.),
                                          (q, k.expand(1, 3, 2, 8), v.expand(1, 3, 2, 8), 1.)):
            with self.subTest(surface='sensitivity'), self.assertRaises(ValueError):
                codec.attention_sensitivity(query, keys, values, scale)

    @unittest.skipUnless(torch.cuda.is_available(), 'requires a qualified CUDA/HIP device')
    def test_device_preservation_on_gpu(self):
        tensor = torch.zeros(1, 1, 2, 8, device='cuda')
        result = codec.roundtrip(tensor, torch.zeros(4, 256, 8, device='cuda'))
        self.assertIsNotNone(result)
        self.assertTrue(all(item.device == tensor.device for item in result))
        sensitivity = codec.attention_sensitivity(tensor[:, :, :1], tensor, tensor, 0.5)
        self.assertIsNotNone(sensitivity)
        self.assertTrue(all(item.device == tensor.device for item in sensitivity))


if __name__ == '__main__':
    unittest.main()
