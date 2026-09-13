"""Checks for split isolation, real Q capture and dictionary wire semantics."""
import copy
import os
import sys
import unittest

os.environ['USE_TF'] = '0'
os.environ['USE_FLAX'] = '0'
sys.path.append('/host-deps')

import torch
from dictionary_probe import (validate_documents, fit_dictionaries, codewords,
                              dictionary_roundtrip, capture_final_qkv)


class DictionaryTests(unittest.TestCase):
    def test_split_identity_boundary(self):
        docs = [dict(id=str(i), group=str(i), split=s, text='document ' + str(i))
                for i, s in enumerate(('calibration', 'selection', 'evaluation'))]
        self.assertTrue(validate_documents(docs))
        for field, value in [('id', '0'), ('group', '0'), ('text', ' DOCUMENT   0 '),
                             ('split', 'unknown'), ('text', '')]:
            bad = copy.deepcopy(docs)
            bad[1][field] = value
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    validate_documents(bad)

    def test_fitting_is_seeded_and_unit_length(self):
        x = torch.randn(512, 8, generator=torch.Generator().manual_seed(7))
        original = x.clone()
        result = fit_dictionaries(x, 4827)
        self.assertIsNotNone(result)
        repeat = fit_dictionaries(x, 4827)
        for kind, shape in [('vq', (256, 8)), ('segments', (32, 2, 8))]:
            self.assertEqual(result[kind].shape, shape)
            self.assertEqual(result[kind].dtype, torch.float32)
            self.assertTrue(torch.equal(result[kind], repeat[kind]))
            self.assertTrue(torch.allclose(result[kind].norm(dim=-1), torch.ones(shape[:-1]), atol=1e-6))
            expanded = codewords(kind, result[kind])
            self.assertEqual(expanded.shape, (256, 8))
            self.assertTrue(torch.allclose(expanded.norm(dim=-1), torch.ones(256), atol=1e-6))
        self.assertTrue(torch.equal(x, original))

    def test_wire_roundtrip_and_normalized_segments(self):
        e1, e2 = torch.eye(8)[:2]
        table = e1.repeat(256, 1)
        table[1] = e2
        x = torch.stack((2 * e1, 3 * e2, torch.zeros(8)))
        result = dictionary_roundtrip(x, 'vq', table)
        self.assertIsNotNone(result)
        restored, indices, radii = result
        self.assertTrue(torch.equal(restored, x))
        self.assertEqual(indices.dtype, torch.uint8)
        self.assertEqual(indices.tolist(), [0, 1, 0])
        self.assertEqual(radii.dtype, torch.bfloat16)
        self.assertEqual(indices.numel() + radii.numel() * radii.element_size(), 9)
        segments = torch.stack((e1, e2)).repeat(32, 1, 1)
        expected = (4 * e1 + 3 * e2) / 5
        self.assertTrue(torch.allclose(codewords('segments', segments)[3], expected, atol=1e-6))
        restored, indices, radii = dictionary_roundtrip(expected[None], 'segments', segments)
        self.assertEqual(indices.item(), 3)
        self.assertTrue(torch.allclose(restored[0], expected, atol=1e-6))

    def test_invalid_dictionary_inputs(self):
        e1 = torch.eye(8)[0]
        for x in (torch.ones(2, 7), torch.full((2, 8), float('nan')), torch.zeros(0, 8)):
            with self.assertRaises(ValueError):
                dictionary_roundtrip(x, 'vq', e1.repeat(256, 1))
        with self.assertRaises(ValueError):
            fit_dictionaries(torch.ones(255, 8), 4827)
        with self.assertRaises(ValueError):
            codewords('vq', torch.zeros(256, 8))
        with self.assertRaises(ValueError):
            codewords('other', e1.repeat(256, 1))

    def test_final_query_matches_actual_attention(self):
        from transformers import Qwen3Config, Qwen3ForCausalLM
        torch.manual_seed(4827)
        cfg = Qwen3Config(vocab_size=32, hidden_size=64, intermediate_size=64,
                          num_hidden_layers=2, num_attention_heads=2,
                          num_key_value_heads=1, head_dim=32)
        cfg._attn_implementation = 'eager'
        model = Qwen3ForCausalLM(cfg).eval()
        captures = capture_final_qkv(model, torch.tensor([[1, 2, 3, 4, 5, 6]]))
        self.assertIsNotNone(captures)
        self.assertEqual(len(captures), 2)
        for c in captures:
            self.assertEqual(c['q'].shape, (1, 2, 1, 32))
            self.assertEqual(c['k'].shape, (1, 1, 6, 32))
            self.assertEqual(c['v'].shape, (1, 1, 6, 32))
            self.assertLessEqual(c['oracle_max_error'], 1e-6)
            self.assertAlmostEqual(c['scale'], 32 ** -0.5)


if __name__ == '__main__':
    unittest.main()
