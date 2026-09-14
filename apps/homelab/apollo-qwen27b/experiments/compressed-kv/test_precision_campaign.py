"""Fresh-input, calibration and capture gates for the precision continuation."""
import hashlib
import unittest

import torch
from transformers import Qwen3Config, Qwen3ForCausalLM

from precision_campaign import capture_precision, fit_levels, freeze_documents, select_candidate


class PrecisionCampaignTests(unittest.TestCase):
    def test_frozen_documents_are_unique_ranked_and_disjoint(self):
        rows = [dict(platform='fixture', question_id=str(i), question_content='x' * (270 + i))
                for i in range(24)]
        rows.append(dict(platform='fixture', question_id='short', question_content='x'))
        encode = lambda text: [len(text)] + list(range(len(text) - 1))
        actual = freeze_documents(rows, encode)
        self.assertIsInstance(actual, list)
        self.assertEqual(actual, freeze_documents(list(reversed(rows)), encode))
        self.assertEqual(len(actual), 18)
        self.assertEqual([r['split'] for r in actual], ['calibration'] * 6 + ['selection'] * 6 + ['evaluation'] * 6)
        expected = sorted(rows[:-1], key=lambda r: hashlib.sha256(
            ('4827:' + r['platform'] + ':' + r['question_id']).encode()).hexdigest())[:18]
        self.assertEqual([r['id'] for r in actual], ['fixture:' + r['question_id'] for r in expected])
        self.assertEqual([r['token_ids'] for r in actual], [encode(r['question_content'])[:257] for r in expected])
        self.assertTrue(all(len(r['text_sha256']) == 64 and len(r['tokens_sha256']) == 64 for r in actual))
        with self.assertRaises(ValueError):
            freeze_documents(rows + [rows[0]], encode)
        with self.assertRaises(ValueError):
            freeze_documents(rows[:2], encode)
        # Distinct documents with the same used prefix cannot form a holdout.
        with self.assertRaises(ValueError):
            freeze_documents(rows, lambda text: list(range(len(text))))

    def test_candidate_selection_uses_all_calibration_documents_and_stable_ties(self):
        candidates = [{'clip': 0.5}, {'clip': 1.0}, {'clip': 0.9}]
        self.assertEqual(select_candidate(candidates, [[0., 4.], [1., 1.], [1., 1.]]), candidates[1])
        for losses in ([[1.]], [[float('nan'), 1.]] * 3, [[-1., 0.]] * 3):
            with self.assertRaises(ValueError):
                select_candidate(candidates, losses)

    def test_learned_levels_are_frozen_finite_and_seeded(self):
        values = torch.full((256,), 0.9)
        levels = fit_levels(values, 4827)
        self.assertIsInstance(levels, torch.Tensor)
        self.assertEqual(tuple(levels.shape), (7,))
        self.assertTrue(torch.equal(levels, fit_levels(values, 4827)))
        self.assertTrue(torch.isfinite(levels).all() and (levels > 0).all() and (levels <= 1).all())
        self.assertLess(float((levels - 0.9).abs().min()), 1e-6)
        self.assertTrue(torch.equal(levels, levels.sort().values))
        self.assertTrue(torch.isfinite(fit_levels(torch.zeros(64), 4827)).all())
        for bad in (torch.empty(0), torch.tensor([float('inf')]), torch.tensor([-0.1]), torch.tensor([1.1])):
            with self.assertRaises(ValueError):
                fit_levels(bad, 4827)

    def test_longer_capture_matches_actual_attention_without_mutating_model(self):
        torch.manual_seed(4827)
        config = Qwen3Config(vocab_size=96, hidden_size=32, intermediate_size=48,
                             num_hidden_layers=2, num_attention_heads=4,
                             num_key_value_heads=2, head_dim=8, max_position_embeddings=512,
                             attention_dropout=0.0)
        config._attn_implementation = 'eager'
        model = Qwen3ForCausalLM(config).float().eval()
        ids = torch.arange(96).remainder(91).reshape(1, -1)
        before = {k: v.clone() for k, v in model.state_dict().items()}
        result = capture_precision(model, ids)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        for layer in result:
            self.assertEqual(tuple(layer['q'].shape), (1, 4, 1, 8))
            self.assertEqual(tuple(layer['k'].shape), (1, 2, 96, 8))
            self.assertEqual(tuple(layer['v'].shape), (1, 2, 96, 8))
            self.assertLessEqual(layer['oracle_max_error'], 1e-6)
            self.assertEqual(layer['k'].device.type, 'cpu')
        self.assertTrue(all(torch.equal(v, before[k]) for k, v in model.state_dict().items()))
        with self.assertRaises(ValueError):
            capture_precision(model, torch.ones(1, 257, dtype=torch.long))


if __name__ == '__main__':
    unittest.main()
