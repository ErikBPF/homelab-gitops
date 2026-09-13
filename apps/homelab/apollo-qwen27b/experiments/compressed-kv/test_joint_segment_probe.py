"""Lock final-iterate fitting and reproduction of the retained controls."""
import copy
import unittest

import torch
from joint_segment_probe import refine_segments, verify_replay


class JointSegmentTests(unittest.TestCase):
    def test_refinement_uses_final_iterate_without_mutating_inputs(self):
        e1, e2 = torch.eye(8)[:2]
        initial = e1.repeat(32, 2, 1)
        vectors = (0.6 * e1 + 0.8 * e2).repeat(256, 1)
        saved_table, saved_vectors = initial.clone(), vectors.clone()
        result = refine_segments(vectors, initial)
        self.assertIsNotNone(result)
        table, losses = result
        self.assertEqual(table.shape, (32, 2, 8))
        self.assertEqual(table.dtype, torch.float32)
        self.assertFalse(table.requires_grad)
        self.assertEqual(len(losses), 51)
        self.assertTrue(torch.allclose(table.norm(dim=-1), torch.ones(32, 2), atol=1e-6))
        self.assertLess(losses[-1], losses[0] / 2)
        from dictionary_probe import codewords
        words = codewords('segments', table)
        selected = words[(vectors @ words.T).argmax(dim=1)]
        self.assertAlmostEqual(losses[-1], (vectors - selected).square().mean().item(), places=6)
        self.assertTrue(torch.equal(initial, saved_table))
        self.assertTrue(torch.equal(vectors, saved_vectors))

    def test_invalid_refinement_inputs(self):
        with self.assertRaises(ValueError):
            refine_segments(torch.ones(256, 8), torch.zeros(32, 2, 8))
        with self.assertRaises(ValueError):
            refine_segments(torch.full((256, 8), float('nan')), torch.eye(8)[0].repeat(32, 2, 1))

    def test_replay_checks_identity_counts_bytes_and_finite_metrics(self):
        expected = [dict(event='comparison', document_id='evaluation_fixture',
                         family='segments', seed=4827, mode='kv',
                         attention_nmse=0.1, raw_bytes=100, matched_bytes=200)]
        self.assertTrue(verify_replay(copy.deepcopy(expected), expected))
        changed = copy.deepcopy(expected)
        changed[0]['attention_nmse'] += 1e-9
        self.assertTrue(verify_replay(changed, expected))
        for key, value in [('attention_nmse', 0.2), ('attention_nmse', float('nan')),
                           ('raw_bytes', 101), ('mode', 'k')]:
            changed = copy.deepcopy(expected)
            changed[0][key] = value
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    verify_replay(changed, expected)
        with self.assertRaises(ValueError):
            verify_replay([], expected)
        with self.assertRaises(ValueError):
            verify_replay(expected * 2, expected)


if __name__ == '__main__':
    unittest.main()
