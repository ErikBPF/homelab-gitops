"""Fresh-input, calibration and capture gates for the precision continuation."""
import hashlib
import unittest
from unittest.mock import patch

import torch
from transformers import Qwen3Config, Qwen3ForCausalLM

from precision_campaign import capture_precision, fit_levels, freeze_documents, select_candidate


class PrecisionCampaignTests(unittest.TestCase):
    def test_capture_uses_actual_full_sequence_queries_and_removes_hooks(self):
        torch.manual_seed(4827)
        config = Qwen3Config(vocab_size=32, hidden_size=32, intermediate_size=48,
                            num_hidden_layers=2, num_attention_heads=4,
                            num_key_value_heads=2, head_dim=8, attention_dropout=0.0)
        config._attn_implementation = 'eager'
        model = Qwen3ForCausalLM(config).float().eval()
        ids = torch.arange(17).reshape(1, -1)
        queries, rotary = {}, []
        handles = []

        def perturb(index):
            def hook(module, args, output):
                # A full-sequence-only change makes last-token reprojection invalid.
                actual = output + (0.5 if output.shape[1] > 1 else 0.0)
                queries[index] = actual.detach().clone()
                return actual
            return hook

        for index, layer in enumerate(model.model.layers):
            handles.append(layer.self_attn.q_norm.register_forward_hook(perturb(index)))
        handles.append(model.model.rotary_emb.register_forward_hook(
            lambda module, args, output: rotary.append(tuple(x.detach().clone() for x in output))))
        counts = {name: len(module._forward_hooks) for name, module in model.named_modules()}
        try:
            try:
                captured = capture_precision(model, ids)
            except AssertionError as error:
                self.fail(f'capture recomputed a different query: {error}')
            self.assertEqual(len(rotary), 1)
            cos, sin = (x.unsqueeze(1) for x in rotary[0])
            for index, layer in enumerate(captured):
                q = queries[index].transpose(1, 2)
                rotated = torch.cat((-q[..., 4:], q[..., :4]), dim=-1)
                expected = (q * cos + rotated * sin)[:, :, -1:, :]
                self.assertTrue(torch.equal(layer['q'], expected))
            self.assertEqual(counts, {name: len(module._forward_hooks)
                                      for name, module in model.named_modules()})
            with patch.object(model, 'forward', side_effect=RuntimeError('fixture failure')):
                with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
                    capture_precision(model, ids)
            self.assertEqual(counts, {name: len(module._forward_hooks)
                                      for name, module in model.named_modules()})
        finally:
            for handle in handles:
                handle.remove()

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


    @unittest.skipUnless(torch.cuda.device_count() >= 2, 'requires two CUDA devices')
    def test_capture_restores_explicitly_dispatched_rotary_output_device(self):
        from accelerate.hooks import AlignDevicesHook, add_hook_to_module
        torch.manual_seed(4827)
        config = Qwen3Config(vocab_size=96, hidden_size=32, intermediate_size=48,
                             num_hidden_layers=2, num_attention_heads=4,
                             num_key_value_heads=2, head_dim=8, max_position_embeddings=512,
                             attention_dropout=0.0)
        config._attn_implementation = 'eager'
        model = Qwen3ForCausalLM(config).float().eval().to('cuda:0')
        add_hook_to_module(model.model.rotary_emb, AlignDevicesHook(execution_device='cuda:1'))
        for layer in model.model.layers:
            add_hook_to_module(layer, AlignDevicesHook(execution_device='cuda:0'))
        ids = torch.arange(17, device='cuda:0').reshape(1, -1)
        with torch.inference_mode():
            self.assertTrue(torch.isfinite(model(ids).logits).all())
        try:
            captured = capture_precision(model, ids)
        except RuntimeError as error:
            self.fail(f'capture must handle independently dispatched rotary output: {error}')
        self.assertEqual(len(captured), 2)
        self.assertTrue(all(layer['oracle_max_error'] <= 1e-6 for layer in captured))


if __name__ == '__main__':
    unittest.main()
