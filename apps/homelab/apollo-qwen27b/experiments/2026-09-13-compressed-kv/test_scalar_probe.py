"""Scalar controls: signed wire format, distortion bound and input boundary."""
import unittest
import sys
import math

sys.path.append('/host-deps')

import torch

from scalar_probe import scalar_roundtrip, evaluate_prefix


class ScalarTests(unittest.TestCase):
    def test_all_variants_on_real_tiny_attention(self):
        from transformers import Qwen3Config, Qwen3ForCausalLM
        torch.manual_seed(4827)
        config = Qwen3Config(vocab_size=32, hidden_size=64, intermediate_size=64,
                            num_hidden_layers=2, num_attention_heads=2,
                            num_key_value_heads=1, head_dim=32)
        config._attn_implementation = 'eager'
        model = Qwen3ForCausalLM(config).eval()
        result = evaluate_prefix(model, torch.tensor([[1, 2, 3, 4, 5, 6]]))
        self.assertIsNotNone(result)
        self.assertEqual(result['prefix_tokens'], 5)
        self.assertEqual(result['baseline_fp32_bytes'], 2560)
        self.assertEqual(result['baseline_bf16_bytes'], 1280)
        self.assertTrue(result['cache_unchanged'])
        self.assertLess(result['baseline_full_logit_max_error'], 1e-4)
        self.assertEqual({(v['bits'], v['mode']) for v in result['variants']},
                         {(b, m) for b in (2, 4, 8, 16) for m in ('k', 'v', 'kv')})
        self.assertEqual(len(result['variants']), 12)
        for v in result['variants']:
            for key in ('kl_original_to_variant', 'logit_rmse', 'k_nmse', 'v_nmse'):
                self.assertTrue(math.isfinite(v[key]), (v['bits'], v['mode'], key))
            side = 640 if v['bits'] == 16 else 320 * v['bits'] // 8 + 40
            self.assertEqual(v['stored_bytes'], 2 * side if v['mode'] == 'kv' else side + 1280)
            self.assertGreaterEqual(v['kl_original_to_variant'], -1e-6)
            self.assertGreaterEqual(v['logit_rmse'], 0)

    def test_signed_golden_and_zero_group(self):
        x = torch.tensor([[-7., 7., -1., 1.] * 8, [0.] * 32])
        result = scalar_roundtrip(x, 4)
        self.assertIsNotNone(result)
        restored, packed, scales = result
        self.assertEqual(bytes(packed.tolist()), bytes.fromhex('791f' * 8 + '00' * 16))
        self.assertTrue(torch.equal(scales.flatten(), torch.ones(2)))
        self.assertTrue(torch.equal(restored, x))

    def test_widths_storage_error_and_no_mutation(self):
        x = torch.linspace(-3, 3, 256, dtype=torch.float32).reshape(2, 1, 128)
        original = x.clone()
        for bits in (2, 4, 8):
            with self.subTest(bits=bits):
                result = scalar_roundtrip(x, bits)
                self.assertIsNotNone(result)
                restored, packed, scales = result
                self.assertEqual(restored.shape, x.shape)
                self.assertEqual(restored.dtype, x.dtype)
                self.assertEqual(packed.dtype, torch.uint8)
                self.assertEqual(packed.numel(), x.numel() * bits // 8)
                self.assertEqual(scales.dtype, torch.float32)
                self.assertEqual(scales.numel() * scales.element_size(), 32)
                self.assertLessEqual((restored - x).abs().max().item(), 3 / (2 ** (bits - 1) - 1) / 2 + 1e-6)
                self.assertTrue(torch.equal(x, original))

    def test_invalid_inputs(self):
        valid = torch.zeros(32)
        for x, bits in [(valid, 3), (valid, True), (valid, 4.0),
                        (torch.zeros(31), 4), (torch.tensor(1.), 4),
                        (torch.empty(0, 32), 4), (torch.ones(32, dtype=torch.int64), 4),
                        (torch.ones(32, dtype=torch.float64), 4),
                        (torch.full((32,), float('nan')), 4),
                        (torch.full((32,), float('inf')), 4)]:
            with self.subTest(shape=x.shape, bits=bits):
                with self.assertRaises(ValueError):
                    scalar_roundtrip(x, bits)


if __name__ == '__main__':
    unittest.main()
