"""Lock Cartesian magnitudes, affine interpolation and physical code accounting."""

import math
import os
import sys
import unittest

os.environ['USE_TF'] = '0'
os.environ['USE_FLAX'] = '0'
sys.path.append('/host-deps')

import torch
import cartesian_probe as codec


class CartesianTests(unittest.TestCase):
    def test_finite_distance_overflow_is_an_explicit_blocker(self):
        vectors = torch.zeros(1, 8)
        vectors[0, 0] = 3e20
        table = torch.zeros(256, 8)
        table[:, 0] = -3e20
        table[1, 0] = 2e20
        with self.assertRaises(ValueError):
            codec.roundtrip(vectors, 'vq', table)

    def test_affine_words_and_euclidean_wire_preserve_magnitudes(self):
        e1 = torch.eye(8)[0]
        table = torch.zeros(256, 8)
        table[:3] = torch.stack((e1, 2 * e1, -3 * e1))
        vectors = torch.stack((e1, 2 * e1, -3 * e1, torch.zeros(8)))
        saved_vectors, saved_table = vectors.clone(), table.clone()
        result = codec.roundtrip(vectors, 'vq', table)
        self.assertIsNotNone(result)
        restored, codes = result
        self.assertEqual(codes.dtype, torch.uint8)
        self.assertEqual(codes.tolist(), [0, 1, 2, 3])
        self.assertEqual(codes.numel() * codes.element_size(), 4)
        self.assertTrue(torch.equal(restored, vectors))
        self.assertEqual(restored.dtype, torch.float32)
        self.assertEqual(restored.shape, vectors.shape)
        self.assertTrue(torch.equal(vectors, saved_vectors))
        self.assertTrue(torch.equal(table, saved_table))
        segments = torch.stack((-4 * e1, 3 * e1)).repeat(32, 1, 1)
        words = codec.codewords('segments', segments)
        self.assertIsNotNone(words)
        self.assertTrue(torch.allclose(words[:8], torch.arange(-4, 4)[:, None] * e1, atol=1e-6))
        self.assertTrue(torch.equal(codec.codewords('vq', table), table))
        zero_result = codec.roundtrip(torch.zeros(1, 8), 'segments', torch.zeros(32, 2, 8))
        self.assertIsNotNone(zero_result)
        self.assertEqual(zero_result[1].item(), 0)
        self.assertTrue(torch.equal(zero_result[0], torch.zeros(1, 8)))

    def test_seeded_fit_retains_nonunit_constant_centers(self):
        vectors = torch.tensor([2.5, -1.5, 0, 3, 0, 0, 0, 0]).repeat(512, 1)
        saved = vectors.clone()
        fitted = codec.fit_dictionaries(vectors, 4827)
        self.assertIsNotNone(fitted)
        repeat = codec.fit_dictionaries(vectors, 4827)
        for kind, shape in (('vq', (256, 8)), ('segments', (32, 2, 8))):
            self.assertEqual(tuple(fitted[kind].shape), shape)
            self.assertEqual(fitted[kind].dtype, torch.float32)
            self.assertTrue(torch.equal(fitted[kind], vectors[0].expand(shape)))
            self.assertTrue(torch.equal(fitted[kind], repeat[kind]))
        self.assertTrue(torch.equal(vectors, saved))

    def test_joint_fit_returns_final_cartesian_objective(self):
        initial = torch.zeros(32, 2, 8)
        vectors = torch.tensor([2., -1., 0, 0, 0, 0, 0, 0]).repeat(256, 1)
        saved_initial, saved_vectors = initial.clone(), vectors.clone()
        result = codec.refine_segments(vectors, initial)
        self.assertIsNotNone(result)
        table, losses = result
        self.assertEqual(tuple(table.shape), (32, 2, 8))
        self.assertEqual(table.dtype, torch.float32)
        self.assertFalse(table.requires_grad)
        self.assertEqual(len(losses), 51)
        self.assertTrue(all(math.isfinite(loss) for loss in losses))
        self.assertAlmostEqual(losses[0], vectors.square().mean().item(), places=6)
        self.assertLess(losses[-1], losses[0] / 2)
        alpha = torch.arange(8)[None, :, None] / 7
        words = ((1 - alpha) * table[:, :1] + alpha * table[:, 1:]).reshape(256, 8)
        nearest = (vectors[:, None] - words[None]).square().sum(dim=-1).argmin(dim=1)
        self.assertAlmostEqual(losses[-1], (vectors - words[nearest]).square().mean().item(), places=6)
        self.assertTrue(torch.equal(initial, saved_initial))
        self.assertTrue(torch.equal(vectors, saved_vectors))

    def test_lut_matches_all_affine_scores_without_radius(self):
        generator = torch.Generator().manual_seed(4827)
        table = torch.randn(32, 2, 8, generator=generator) * 3
        saved = table.clone()
        alpha = torch.arange(8)[None, :, None] / 7
        words = ((1 - alpha) * table[:, :1] + alpha * table[:, 1:]).reshape(256, 8)
        for _ in range(2):
            query = torch.randn(8, generator=generator)
            result = codec.score_lut(query, table)
            self.assertIsNotNone(result)
            self.assertEqual(tuple(result.shape), (256,))
            self.assertTrue(torch.allclose(result, words @ query, atol=1e-5, rtol=1e-5))
        self.assertTrue(torch.equal(table, saved))

    def test_invalid_inputs_raise_value_error(self):
        table = torch.zeros(256, 8)
        for vectors in (torch.zeros(0, 8), torch.zeros(2, 7), torch.ones(2, 8, dtype=torch.float64),
                        torch.full((2, 8), float('nan')), torch.ones(2, 8, device='meta')):
            with self.subTest(shape=tuple(vectors.shape), dtype=vectors.dtype, device=vectors.device):
                with self.assertRaises(ValueError):
                    codec.roundtrip(vectors, 'vq', table)
        for kind, invalid in (('other', table), ('vq', torch.zeros(255, 8)),
                              ('vq', table.double()), ('segments', torch.full((32, 2, 8), float('inf')))):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                codec.codewords(kind, invalid)
        with self.assertRaises(ValueError):
            codec.fit_dictionaries(torch.ones(255, 8), 4827)
        with self.assertRaises(ValueError):
            codec.refine_segments(torch.full((256, 8), float('nan')), torch.zeros(32, 2, 8))
        for query in (torch.ones(7), torch.full((8,), float('inf')), torch.ones(8, dtype=torch.float64)):
            with self.assertRaises(ValueError):
                codec.score_lut(query, torch.zeros(32, 2, 8))

    def test_tiny_qwen_attention_and_actual_bytes_match_manual_cartesian_decode(self):
        from transformers import Qwen3Config, Qwen3ForCausalLM
        from dictionary_probe import capture_final_qkv
        torch.manual_seed(4827)
        config = Qwen3Config(vocab_size=32, hidden_size=64, intermediate_size=64,
                            num_hidden_layers=2, num_attention_heads=2,
                            num_key_value_heads=1, head_dim=32)
        config._attn_implementation = 'eager'
        captures = capture_final_qkv(Qwen3ForCausalLM(config).eval(), torch.tensor([[1, 2, 3, 4, 5, 6]]))
        saved = [{side: c[side].clone() for side in ('q', 'k', 'v')} for c in captures]
        generator = torch.Generator().manual_seed(91)
        for family, shape in (('vq', (256, 8)), ('segments', (32, 2, 8))):
            tables = {side: torch.randn(shape, generator=generator) for side in ('k', 'v')}
            words = {}
            for side, table in tables.items():
                alpha = torch.arange(8)[None, :, None] / 7
                words[side] = table if family == 'vq' else ((1 - alpha) * table[:, :1] + alpha * table[:, 1:]).reshape(256, 8)
            for mode in ('k', 'v', 'kv'):
                result = codec.comparison(captures, family, tables, mode)
                self.assertIsNotNone(result)
                errors = dict(k=0., v=0., attention=0.)
                energies = errors.copy()
                for c in captures:
                    decoded = {}
                    for side in ('k', 'v'):
                        original = c[side]
                        vectors = original.reshape(-1, 8)
                        nearest = (vectors[:, None] - words[side][None]).square().sum(dim=-1).argmin(dim=1)
                        decoded[side] = words[side][nearest].reshape(original.shape) if side in mode else original
                        errors[side] += (original.double() - decoded[side].double()).square().sum().item()
                        energies[side] += original.double().square().sum().item()
                    outputs = []
                    for values in (c, decoded):
                        k = values['k'].repeat_interleave(2, dim=1)
                        v = values['v'].repeat_interleave(2, dim=1)
                        weights = ((c['q'] @ k.transpose(-2, -1)) * c['scale']).softmax(dim=-1)
                        outputs.append(weights @ v)
                    errors['attention'] += (outputs[0].double() - outputs[1].double()).square().sum().item()
                    energies['attention'] += outputs[0].double().square().sum().item()
                for name in errors:
                    self.assertTrue(math.isfinite(result[name + '_nmse']))
                    self.assertAlmostEqual(result[name + '_nmse'], errors[name] / energies[name], places=6)
                sides = len(mode)
                table_bytes = sides * (8192 if family == 'vq' else 2048)
                untouched = (2 - sides) * 1536
                self.assertEqual(result['baseline_fp32_bytes'], 3072)
                self.assertEqual(result['payload_bytes'], sides * 48)
                self.assertEqual(result['scale_bytes'], 0)
                self.assertEqual(result['table_bytes'], table_bytes)
                self.assertEqual(result['untouched_fp32_bytes'], untouched)
                self.assertEqual(result['raw_bytes'], sides * 48 + table_bytes + untouched)
                self.assertEqual(result['matched_bytes'], sides * (48 + 8192) + untouched)
                self.assertEqual(result['table_padding_bytes'], sides * 8192 - table_bytes)
                self.assertEqual(result['expanded_decode_working_bytes'], sides * 8192)
        self.assertTrue(all(torch.equal(c[side], original[side]) for c, original in zip(captures, saved)
                            for side in ('q', 'k', 'v')))


if __name__ == '__main__':
    unittest.main()
