"""Lock causal propagation, append isolation and aligned logit measurements."""

import math
import unittest

from teacher_forced_probe import QuantizedCache, evaluate_teacher_forced, logit_metrics
import torch
from transformers import Qwen3Config, Qwen3ForCausalLM


def tiny_model():
    torch.manual_seed(4827)
    config = Qwen3Config(vocab_size=32, hidden_size=64, intermediate_size=64,
                        num_hidden_layers=2, num_attention_heads=2,
                        num_key_value_heads=1, head_dim=32)
    config._attn_implementation = 'eager'
    return Qwen3ForCausalLM(config).eval()


class TeacherForcedTests(unittest.TestCase):
    @torch.inference_mode()
    def test_full_causal_matches_append_for_nonlinear_and_learned_codecs(self):
        from cartesian_probe import roundtrip

        model = tiny_model()
        ids = torch.tensor([[1, 2, 3, 4, 5, 6]])
        table = torch.randn(256, 8, generator=torch.Generator().manual_seed(7)) * 0.3

        def nonlinear(tensor, side):
            return tensor + 0.2 * tensor.square()

        def learned(tensor, side):
            return roundtrip(tensor.reshape(-1, 8), 'vq', table)[0].reshape(tensor.shape)

        for codec in (nonlinear, learned):
            for mode in ('k', 'v', 'kv'):
                with self.subTest(codec=codec.__name__, mode=mode):
                    result = evaluate_teacher_forced(model, ids, codec, mode)
                    self.assertIsNotNone(result)
                    logits, full_cache = result
                    appended = QuantizedCache(model.config, codec, mode)
                    parts = [model(input_ids=ids[:, :3], past_key_values=appended,
                                   use_cache=True).logits]
                    for position in range(3, ids.shape[1] - 1):
                        saved = [(layer.keys.clone(), layer.values.clone())
                                 for layer in appended.layers]
                        parts.append(model(input_ids=ids[:, position:position + 1],
                                           past_key_values=appended, use_cache=True).logits)
                        for layer, (keys, values) in zip(appended.layers, saved):
                            self.assertTrue(torch.equal(layer.keys[..., :keys.shape[-2], :], keys))
                            self.assertTrue(torch.equal(layer.values[..., :values.shape[-2], :], values))
                    self.assertTrue(torch.allclose(logits, torch.cat(parts, dim=1), atol=1e-4, rtol=1e-4))
                    for full, stepped in zip(full_cache.layers, appended.layers):
                        self.assertTrue(torch.allclose(full.keys, stepped.keys, atol=1e-4, rtol=1e-4))
                        self.assertTrue(torch.allclose(full.values, stepped.values, atol=1e-4, rtol=1e-4))

    def test_append_never_requantizes_existing_entries(self):
        cache = QuantizedCache(tiny_model().config, lambda tensor, side: tensor + 1, 'kv')
        keys, values = torch.full((1, 1, 2, 32), 2.0), torch.full((1, 1, 2, 32), 5.0)
        old_k, old_v = keys.clone(), values.clone()
        first_k, first_v = cache.update(keys, values, 0)
        self.assertTrue(torch.equal(first_k, keys + 1))
        self.assertTrue(torch.equal(first_v, values + 1))
        next_k, next_v = cache.update(keys[..., :1, :] * 2, values[..., :1, :] * 2, 0)
        self.assertTrue(torch.equal(next_k[..., :2, :], first_k))
        self.assertTrue(torch.equal(next_v[..., :2, :], first_v))
        self.assertTrue(torch.equal(next_k[..., 2:, :], keys[..., :1, :] * 2 + 1))
        self.assertTrue(torch.equal(next_v[..., 2:, :], values[..., :1, :] * 2 + 1))
        self.assertTrue(torch.equal(keys, old_k))
        self.assertTrue(torch.equal(values, old_v))

    def test_incoming_side_isolation_and_independent_caches(self):
        config = tiny_model().config
        keys, values = torch.ones(1, 1, 1, 32), torch.full((1, 1, 1, 32), 2.0)
        for mode in ('k', 'v', 'kv'):
            with self.subTest(mode=mode):
                codec = lambda tensor, side: tensor + (3 if side == 'k' else 4)
                first, second = QuantizedCache(config, codec, mode), QuantizedCache(config, codec, mode)
                a, b = first.update(keys, values, 0)
                c, d = second.update(keys, values, 0)
                self.assertTrue(torch.equal(a, keys + (3 if 'k' in mode else 0)))
                self.assertTrue(torch.equal(b, values + (4 if 'v' in mode else 0)))
                saved = c.clone(), d.clone()
                a.add_(10)
                b.add_(10)
                self.assertTrue(torch.equal(c, saved[0]))
                self.assertTrue(torch.equal(d, saved[1]))
                self.assertTrue(torch.equal(keys, torch.ones_like(keys)))
                self.assertTrue(torch.equal(values, torch.full_like(values, 2)))

    @torch.inference_mode()
    def test_identity_baseline_and_next_token_alignment(self):
        model = tiny_model()
        ids = torch.tensor([[1, 4, 2, 5, 3, 6]])
        saved = ids.clone()
        result = evaluate_teacher_forced(model, ids, lambda tensor, side: tensor, 'kv')
        self.assertIsNotNone(result)
        logits, cache = result
        expected = model(input_ids=ids[:, :-1], use_cache=False).logits
        self.assertEqual(logits.shape, (1, 5, 32))
        self.assertEqual(cache.get_seq_length(), 5)
        self.assertTrue(torch.allclose(logits, expected, atol=1e-4, rtol=1e-4))
        self.assertTrue(torch.equal(ids, saved))
        metrics = logit_metrics(logits, logits, ids[:, 1:])
        self.assertIsNotNone(metrics)
        self.assertEqual(len(metrics), 5)
        for position, row in enumerate(metrics):
            self.assertEqual(row['position'], position)
            self.assertEqual(row['target_token_id'], ids[0, position + 1].item())
            self.assertAlmostEqual(row['kl_reference_to_variant'], 0.0, places=12)
            self.assertAlmostEqual(row['logit_rmse'], 0.0, places=12)
            self.assertAlmostEqual(row['nll_increase'], 0.0, places=12)
            self.assertTrue(row['top_token_agreement'])

    def test_metrics_match_independent_probability_example(self):
        reference = torch.tensor([[[math.log(0.75), math.log(0.25)],
                                   [math.log(0.5), math.log(0.5)]]])
        actual = torch.tensor([[[math.log(0.5), math.log(0.5)],
                                [math.log(0.25), math.log(0.75)]]])
        metrics = logit_metrics(reference, actual, torch.tensor([[1, 1]]))
        self.assertIsNotNone(metrics)
        self.assertEqual(len(metrics), 2)
        for row, p, q in zip(metrics, ((0.75, 0.25), (0.5, 0.5)), ((0.5, 0.5), (0.25, 0.75))):
            self.assertAlmostEqual(row['kl_reference_to_variant'],
                                   sum(a * math.log(a / b) for a, b in zip(p, q)), places=6)
            self.assertAlmostEqual(row['logit_rmse'],
                                   math.sqrt(sum(math.log(a / b) ** 2 for a, b in zip(p, q)) / 2), places=6)
            self.assertAlmostEqual(row['reference_target_nll'], -math.log(p[1]), places=6)
            self.assertAlmostEqual(row['variant_target_nll'], -math.log(q[1]), places=6)
            self.assertAlmostEqual(row['nll_increase'], math.log(p[1] / q[1]), places=6)
        self.assertTrue(metrics[0]['top_token_agreement'])
        self.assertFalse(metrics[1]['top_token_agreement'])

    def test_invalid_modes_shapes_targets_and_nonfinite_logits(self):
        model = tiny_model()
        codec = lambda tensor, side: tensor
        with self.assertRaises(ValueError):
            QuantizedCache(model.config, codec, 'both')
        with self.assertRaises(ValueError):
            evaluate_teacher_forced(model, torch.tensor([[1]]), codec, 'kv')
        reference, targets = torch.zeros(1, 2, 2), torch.tensor([[0, 1]])
        cases = [(reference, reference[:, :1], targets),
                 (reference, reference, targets[:, :1]),
                 (reference, torch.full_like(reference, float('nan')), targets),
                 (torch.full_like(reference, float('inf')), reference, targets),
                 (reference, reference, torch.tensor([[0, 2]])),
                 (reference, reference, targets.float())]
        for expected, actual, labels in cases:
            with self.subTest(shape=actual.shape, targets=labels.tolist()):
                with self.assertRaises(ValueError):
                    logit_metrics(expected, actual, labels)


if __name__ == '__main__':
    unittest.main()
