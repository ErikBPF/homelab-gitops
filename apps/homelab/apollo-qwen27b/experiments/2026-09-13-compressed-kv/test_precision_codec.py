"""Lock four-bit grid replay, grouping axes and delayed-seal causal semantics."""

import os
import sys
import unittest

os.environ['USE_TF'] = '0'
os.environ['USE_FLAX'] = '0'
sys.path.append('/host-deps')

import torch
from transformers import Qwen3Config, Qwen3ForCausalLM
from scalar_probe import scalar_roundtrip
from log_probe import log_roundtrip
import precision_codec as probe


def tiny_model():
    torch.manual_seed(4827)
    config = Qwen3Config(vocab_size=32, hidden_size=64, intermediate_size=64,
                        num_hidden_layers=2, num_attention_heads=2,
                        num_key_value_heads=1, head_dim=32)
    config._attn_implementation = 'eager'
    return Qwen3ForCausalLM(config).eval()


def linear(tensor, side=None):
    return probe.grid_roundtrip(tensor, torch.arange(1, 8, dtype=torch.float32) / 7,
                                legacy_linear=True)


class PrecisionCodecTests(unittest.TestCase):
    @unittest.skipUnless(torch.cuda.is_available(), 'requires CUDA')
    def test_linear_grid_cpu_gpu_construction_preserves_even_ties(self):
        levels = torch.arange(1, 8, dtype=torch.float32) / 7
        for index in range(torch.cuda.device_count()):
            device = torch.device('cuda', index)
            grids = (levels, levels.to(device),
                     torch.arange(1, 8, dtype=torch.float32, device=device) / 7)
            for clip in (1.0, 0.5):
                tensor = torch.tensor([-7 / clip, 7 / clip, 1.5, -1.5,
                                       2.5, -2.5, 3.5, -3.5] * 4, device=device)
                for grid in grids:
                    with self.subTest(device=str(device), clip=clip, grid_device=str(grid.device)):
                        try:
                            decoded, packed, scales = probe.grid_roundtrip(
                                tensor, grid, clip=clip, legacy_linear=True)
                        except ValueError as error:
                            self.fail(f'valid linear grid rejected: {error}')
                        self.assertEqual(bytes(packed.cpu().tolist()), bytes.fromhex('79e2e2c4' * 4))
                        expected = torch.tensor([-7., 7., 2., -2., 2., -2., 4., -4.] * 4, device=device)
                        self.assertTrue(torch.equal(decoded, expected))
                        self.assertEqual(scales.item(), 1.0)
                        self.assertTrue(all(x.device == device for x in (decoded, packed, scales)))
                with self.assertRaises(ValueError):
                    probe.grid_roundtrip(tensor, levels * 0.99, clip=clip, legacy_linear=True)

    def test_historical_wire_replay_and_declared_tie_policies(self):
        levels = probe.log_levels(8)
        self.assertIsNotNone(levels)
        self.assertEqual(levels.shape, (7,))
        self.assertEqual(levels.dtype, torch.float32)
        fixtures = [torch.linspace(-3, 3, 256).reshape(2, 1, 128),
                    torch.tensor([[-7., 7., -1., 1.] * 8, [0.] * 32]),
                    torch.tensor([[-7., 7., -7 / 256, 7 / 256] * 8, [0.] * 32])]
        for tensor in fixtures:
            saved = tensor.clone()
            for codec, reference in ((linear, scalar_roundtrip),
                                      (lambda value: probe.grid_roundtrip(value, levels), log_roundtrip)):
                result = codec(tensor)
                self.assertIsNotNone(result)
                self.assertTrue(all(torch.equal(actual, expected)
                                    for actual, expected in zip(result, reference(tensor, 4))))
            self.assertTrue(torch.equal(tensor, saved))
        clipped = torch.tensor([-7., 7., -1., 1.] * 8)
        decoded, packed, scales = probe.grid_roundtrip(
            clipped, torch.arange(1, 8, dtype=torch.float32) / 7, clip=0.5, legacy_linear=True)
        self.assertEqual(bytes(packed.tolist()), bytes.fromhex('792e' * 8))
        self.assertTrue(torch.equal(decoded, torch.tensor([-3.5, 3.5, -1., 1.] * 8)))
        self.assertEqual(scales.item(), 0.5)
        witness = torch.zeros(32)
        witness[:4] = torch.tensor([-7., 7., 1.5, -1.5])
        decoded, packed, scales = linear(witness)
        self.assertEqual(bytes(packed.tolist()), bytes.fromhex('79e2' + '00' * 14))
        self.assertTrue(torch.equal(decoded[:4], torch.tensor([-7., 7., 2., -2.])))
        self.assertEqual(scales.item(), 1)
        witness[2:4] = torch.tensor([7 / 512, -7 / 512])
        decoded, packed, _ = probe.grid_roundtrip(witness, levels)
        self.assertEqual(bytes(packed.tolist()), bytes.fromhex('79' + '00' * 15))
        self.assertTrue(torch.equal(decoded[2:4], torch.zeros(2)))

    def test_grid_input_parameters_and_device_contract(self):
        levels = torch.arange(1, 8, dtype=torch.float32) / 7
        invalid = [(torch.zeros(31), levels, 1), (torch.zeros(0, 32), levels, 1),
                   (torch.ones(32, dtype=torch.float64), levels, 1),
                   (torch.full((32,), float('nan')), levels, 1),
                   (torch.full((32,), 1e-45), levels, 0.5),
                   (torch.ones(32), torch.ones(6), 1),
                   (torch.ones(32), levels.double(), 1),
                   (torch.ones(32), torch.full((7,), float('inf')), 1),
                   (torch.ones(32), levels, 0), (torch.ones(32), levels, float('nan'))]
        for tensor, grid, clip in invalid:
            with self.subTest(shape=tuple(tensor.shape), clip=clip), self.assertRaises(ValueError):
                probe.grid_roundtrip(tensor, grid, clip)
        for device in (['cpu', 'cuda'] if torch.cuda.is_available() else ['cpu']):
            tensor = torch.linspace(-2, 2, 64, device=device).reshape(2, 32)
            result = probe.grid_roundtrip(tensor, levels)
            self.assertIsNotNone(result)
            decoded, payload, scales = result
            self.assertEqual(decoded.shape, tensor.shape)
            self.assertEqual(decoded.dtype, torch.float32)
            self.assertEqual(payload.dtype, torch.uint8)
            self.assertEqual(scales.dtype, torch.float32)
            self.assertTrue(all(value.device == tensor.device for value in result))
            self.assertEqual(payload.numel() * payload.element_size(), 32)
            self.assertEqual(scales.numel() * scales.element_size(), 8)

    def test_grouping_axis_preserves_layout_and_changes_only_group_members(self):
        tensor = torch.full((1, 1, 32, 32), 0.1)
        tensor[0, 0, 0, 0] = 7
        saved = tensor.clone()
        token = probe.grouped_roundtrip(tensor, linear, 'token')
        channel = probe.grouped_roundtrip(tensor, linear, 'channel')
        self.assertIsNotNone(token)
        self.assertIsNotNone(channel)
        self.assertEqual(token[0].shape, tensor.shape)
        self.assertEqual(channel[0].shape, tensor.shape)
        self.assertEqual(token[0][0, 0, 0, 1].item(), 0)
        self.assertAlmostEqual(channel[0][0, 0, 0, 1].item(), 0.1, places=6)
        self.assertAlmostEqual(token[0][0, 0, 1, 0].item(), 0.1, places=6)
        self.assertEqual(channel[0][0, 0, 1, 0].item(), 0)
        self.assertEqual(token[1].numel(), channel[1].numel())
        self.assertEqual(token[2].numel(), channel[2].numel())
        self.assertTrue(torch.equal(tensor, saved))
        with self.assertRaises(ValueError):
            probe.grouped_roundtrip(tensor[..., :31, :], linear, 'channel')

    def test_delayed_sealing_boundaries_storage_and_old_tensor_immutability(self):
        calls = []

        def codec(tensor, side):
            calls.append((side, tensor.numel()))
            return linear(tensor)

        cache = probe.CausalGroupedCache(tiny_model().config, codec, 'kv', 'channel',
                                         table_bytes={'k': 28, 'v': 28})
        previous = None
        sealed = None
        for n in range(1, 66):
            keys = torch.linspace(0.1, n / 7 + 1, 32).reshape(1, 1, 1, 32)
            values = -keys.clone()
            saved_k, saved_v = keys.clone(), values.clone()
            result = cache.update(keys, values, 0)
            self.assertIsNotNone(result)
            self.assertTrue(torch.equal(keys, saved_k) and torch.equal(values, saved_v))
            if previous is not None:
                self.assertTrue(all(torch.equal(tensor, saved) for tensor, saved in previous))
            previous = [(tensor, tensor.clone()) for tensor in result]
            if n == 33:
                sealed = result[0][..., :32, :].clone()
            if n > 33:
                self.assertTrue(torch.equal(result[0][..., :32, :], sealed))
            if n in (31, 32, 33, 63, 64, 65):
                storage = cache.storage()
                self.assertIsNotNone(storage)
                sealed_tokens = 0 if n <= 32 else 32 if n <= 64 else 64
                expected = dict(payload_bytes=16 * (sealed_tokens + n),
                                scale_bytes=4 * (sealed_tokens + n), table_bytes=56,
                                raw_tail_bytes=(n - sealed_tokens) * 128,
                                untouched_fp32_bytes=0, baseline_fp32_bytes=n * 256,
                                reconstructed_cache_bytes=n * 256)
                expected['raw_bytes'] = sum(expected[key] for key in
                                             ('payload_bytes', 'scale_bytes', 'table_bytes', 'raw_tail_bytes'))
                for key, value in expected.items():
                    self.assertEqual(storage[key], value, (n, key))
        self.assertEqual([numel for side, numel in calls if side == 'k'], [1024, 1024])
        self.assertEqual(len([side for side, _ in calls if side == 'v']), 65)

    def test_modes_independent_caches_zero_tail_and_boundary_rejection(self):
        config = tiny_model().config
        keys = torch.full((1, 1, 32, 32), 0.1)
        keys[..., 0] = 7
        values = -keys.clone()
        saved_k, saved_v = keys.clone(), values.clone()
        a = probe.CausalGroupedCache(config, linear, 'k')
        b = probe.CausalGroupedCache(config, linear, 'v')
        first = a.update(keys, values, 0)
        self.assertIsNotNone(first)
        other = b.update(keys, values, 0)
        self.assertIsNotNone(other)
        self.assertTrue(torch.equal(first[0], keys) and torch.equal(first[1], values))
        self.assertTrue(torch.equal(other[0], keys))
        self.assertFalse(torch.equal(other[1], values))
        other_saved = [tensor.clone() for tensor in other]
        appended = a.update(keys[..., :1, :], values[..., :1, :], 0)
        self.assertIsNotNone(appended)
        self.assertFalse(torch.equal(appended[0][..., :32, :], keys))
        self.assertTrue(torch.equal(appended[1][..., :32, :], values))
        self.assertTrue(all(torch.equal(tensor, saved) for tensor, saved in zip(other, other_saved)))
        self.assertTrue(torch.equal(keys, saved_k) and torch.equal(values, saved_v))
        zero = probe.CausalGroupedCache(config, linear, 'kv', tail_tokens=0)
        actual = zero.update(keys, values, 0)
        self.assertIsNotNone(actual)
        self.assertTrue(torch.equal(actual[0], scalar_roundtrip(keys, 4)[0]))
        self.assertTrue(torch.equal(actual[1], scalar_roundtrip(values, 4)[0]))
        self.assertEqual(zero.storage()['raw_tail_bytes'], 0)
        crossing = probe.CausalGroupedCache(config, linear, 'kv')
        crossing.update(keys[..., :31, :], values[..., :31, :], 0)
        with self.assertRaises(ValueError):
            crossing.update(keys[..., :2, :], values[..., :2, :], 0)
        with self.assertRaises(ValueError):
            probe.CausalGroupedCache(config, linear, 'kv', k_layout='channel', tail_tokens=0)
        config.layer_types = ['sliding_attention'] * 2
        config.sliding_window = 16
        with self.assertRaises(ValueError):
            probe.CausalGroupedCache(config, linear, 'kv')

    @torch.inference_mode()
    def test_65_processed_tokens_match_chunk_one_and_chunk_32(self):
        model = tiny_model()
        ids = (torch.arange(66) % 29 + 1)[None]
        levels = torch.exp2(-8 * (7 - torch.arange(1, 8, dtype=torch.float32)) / 6)

        def codec(tensor, side):
            return probe.grid_roundtrip(tensor, levels)

        for layout in ('token', 'channel'):
            for mode in ('k', 'v', 'kv'):
                with self.subTest(layout=layout, mode=mode):
                    block = probe.evaluate_grouped(model, ids, codec, mode, layout, chunk_size=32)
                    self.assertIsNotNone(block)
                    step = probe.evaluate_grouped(model, ids, codec, mode, layout, chunk_size=1)
                    self.assertIsNotNone(step)
                    self.assertEqual(block[0].shape, (1, 65, 32))
                    self.assertTrue(torch.allclose(block[0], step[0], atol=1e-4, rtol=1e-4))
                    self.assertEqual(block[1].storage(), step[1].storage())
                    for full, stepped in zip(block[1].layers, step[1].layers):
                        self.assertTrue(torch.allclose(full.keys, stepped.keys, atol=1e-4, rtol=1e-4))
                        self.assertTrue(torch.allclose(full.values, stepped.values, atol=1e-4, rtol=1e-4))

    @torch.inference_mode()
    def test_future_outlier_cannot_change_prefix_and_first_k_block_is_clean(self):
        model = tiny_model()
        model.model.embed_tokens.weight[31].fill_(1000)
        ids = (torch.arange(66) % 29 + 1)[None]
        changed = ids.clone()
        changed[0, 31] = 31
        baseline = model(input_ids=ids[:, :-1], use_cache=False).logits
        for layout in ('token', 'channel'):
            original = probe.evaluate_grouped(model, ids, linear, 'k', layout)
            self.assertIsNotNone(original)
            future = probe.evaluate_grouped(model, changed, linear, 'k', layout)
            self.assertIsNotNone(future)
            self.assertTrue(torch.equal(original[0][:, :31], future[0][:, :31]))
            self.assertTrue(torch.allclose(original[0][:, :32], baseline[:, :32], atol=1e-4, rtol=1e-4))


if __name__ == '__main__':
    unittest.main()
