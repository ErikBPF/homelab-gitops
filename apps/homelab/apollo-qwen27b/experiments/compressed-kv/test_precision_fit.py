"""Calibration isolation, frozen comparison and complete precision matrix gates."""

import hashlib
import json
import unittest

import torch

from precision_campaign import compare_frozen, fit_configurations, make_codec, sample_calibration


def capture(tokens=65, seed=4827):
    generator = torch.Generator().manual_seed(seed)
    return dict(q=torch.randn(1, 2, 1, 32, generator=generator),
                k=torch.randn(1, 1, tokens, 32, generator=generator),
                v=torch.randn(1, 1, tokens, 32, generator=generator), scale=32 ** -0.5)


def fixed_config(clip=1.0):
    return dict(id='fixture', family='linear4', seed=None, k_layout='token', tail_tokens=32,
                sides={side: dict(clip=clip, legacy_linear=True) for side in ('k', 'v')},
                parameter_bytes={side: 4 if clip != 1.0 else 0 for side in ('k', 'v')},
                table_bytes=dict(k=0, v=0))


def linear_oracle(tensor, clip):
    groups = tensor.reshape(-1, 32)
    maxima = groups.abs().amax(-1, keepdim=True)
    scales = torch.where(maxima == 0, torch.ones_like(maxima), maxima * clip / 7)
    return ((groups / scales).round().clamp(-7, 7) * scales).reshape(tensor.shape)


def attention_oracle(q, k, v, scale):
    k = k.repeat_interleave(q.shape[1] // k.shape[1], dim=1)
    v = v.repeat_interleave(q.shape[1] // v.shape[1], dim=1)
    return (q.matmul(k.transpose(-1, -2)) * scale).softmax(-1).matmul(v)


class PrecisionFitTests(unittest.TestCase):
    def test_sampling_excludes_holdouts_and_aligns_sensitivities_and_groups(self):
        original = capture(tokens=2305)
        docs = [dict(id='cal:0', split='calibration'), dict(id='selection:0', split='selection'),
                dict(id='eval:0', split='evaluation')]
        samples = sample_calibration(docs, {'cal:0': [original], 'selection:0': object(), 'eval:0': object()})
        self.assertIsInstance(samples, dict)
        self.assertEqual(samples['calibration_ids'], ['cal:0'])
        again = sample_calibration(docs[:1], {'cal:0': [original]})
        q, k, v, scale = (original[name] for name in ('q', 'k', 'v', 'scale'))
        p = (q.matmul(k.repeat_interleave(2, 1).transpose(-1, -2)) * scale).softmax(-1)
        output = p.matmul(v.repeat_interleave(2, 1))
        distance = (v[:, :, None] - output.reshape(1, 1, 2, 1, 32)).square().sum(-1)
        key_weight = (p.reshape(1, 1, 2, -1, 1).square() * scale ** 2
                      * q.reshape(1, 1, 2, 1, 32).square() * distance[..., None]).sum(2)
        value_weight = p.square().sum(1, keepdim=True).transpose(-1, -2).expand_as(v)
        sealed = 2304
        for side, tensor, sensitivity in [('k', k[..., :sealed, :], key_weight[..., :sealed, :]),
                                           ('v', v, value_weight)]:
            indices = samples['vector_indices'][side]
            self.assertEqual(indices.device.type, 'cpu')
            self.assertEqual(indices.numel(), 8192)
            self.assertEqual(indices.unique().numel(), 8192)
            torch.testing.assert_close(samples['vectors'][side], tensor.reshape(-1, 8)[indices], rtol=0, atol=0)
            weights = sensitivity.reshape(-1, 8).mean(-1)[indices]
            positive_mean = weights[weights > 0].double().mean()
            expected = weights.double().clamp_min(positive_mean * 1e-4)
            expected = (expected / expected.mean()).float()
            torch.testing.assert_close(samples['weights'][side], expected)
            self.assertFalse(samples['fallback'][side])
            torch.testing.assert_close(indices, again['vector_indices'][side], rtol=0, atol=0)
            digest = hashlib.sha256(samples['vectors'][side].numpy().tobytes()).hexdigest()
            self.assertEqual(samples['hashes']['vectors'][side], digest)
        pools = dict(k_token=k[..., :sealed, :].reshape(-1, 32),
                     k_channel=k[..., :sealed, :].reshape(1, 1, -1, 32, 32).transpose(-1, -2).contiguous().reshape(-1, 32),
                     v=v.reshape(-1, 32))
        for name, pool in pools.items():
            maxima = pool.abs().amax(-1, keepdim=True)
            normalized = pool.abs() / maxima.clamp_min(torch.finfo(torch.float32).tiny)
            expected = normalized[samples['group_indices'][name]]
            torch.testing.assert_close(samples['groups'][name], expected, rtol=0, atol=0)
            self.assertLessEqual(expected.shape[0], 8192)
            self.assertEqual(samples['pool_sizes']['groups'][name], pool.shape[0])

    def test_zero_query_sampling_reports_uniform_fallback(self):
        original = capture()
        original['q'].zero_()
        sampled = sample_calibration([dict(id='cal', split='calibration')], {'cal': [original]})
        self.assertIsInstance(sampled, dict)
        self.assertTrue(sampled['fallback']['k'])
        self.assertFalse(sampled['fallback']['v'])
        torch.testing.assert_close(sampled['weights']['k'], torch.ones_like(sampled['weights']['k']))

    def test_full_matrix_freezes_only_calibration_and_records_every_candidate(self):
        docs = [dict(id='cal:0', split='calibration'), dict(id='cal:1', split='calibration'),
                dict(id='selection:0', split='selection'), dict(id='eval:0', split='evaluation')]
        result = fit_configurations(docs, {'cal:0': [capture()], 'cal:1': [capture(seed=4828)],
                                           'selection:0': object(), 'eval:0': object()})
        self.assertIsInstance(result, tuple)
        configs, receipts = result
        self.assertEqual(len(configs), 28)
        self.assertEqual(len({config['id'] for config in configs}), 28)
        self.assertEqual(receipts['calibration_ids'], ['cal:0', 'cal:1'])
        json.dumps(receipts, allow_nan=False)
        self.assertEqual(len(receipts['scalar_candidates']), 8)
        for row in receipts['scalar_candidates']:
            self.assertEqual(row['document_ids'], ['cal:0', 'cal:1'])
            expected = [dict(clip=c) for c in (0.5, 0.75, 0.9, 1.0)]
            if row['family'] == 'log4':
                expected = [dict(clip=c, span=s) for c in (0.5, 0.75, 0.9, 1.0) for s in (2, 4, 6, 8, 12)]
            self.assertEqual(row['candidates'], expected)
            self.assertEqual(len(row['losses']), len(expected))
            self.assertTrue(all(len(loss) == 2 for loss in row['losses']))
            index = min(range(len(expected)), key=lambda i: sum(row['losses'][i]) / 2)
            self.assertEqual(row['selected'], expected[index])
        self.assertEqual(sum(c['tail_tokens'] == 0 for c in configs), 2)
        for layout in ('token', 'channel'):
            scalar = [c for c in configs if c['k_layout'] == layout and c['tail_tokens'] == 32
                      and c['family'] != 'residual_vq']
            self.assertEqual(len(scalar), 7)
            learned = [c for c in scalar if c['family'] == 'nonuniform4']
            self.assertEqual({c['seed'] for c in learned}, {4827, 4828, 4829})
            for config in scalar:
                for side in ('k', 'v'):
                    if config['family'] == 'nonuniform4':
                        self.assertEqual(tuple(config['sides'][side]['levels'].shape), (7,))
                        self.assertEqual(config['table_bytes'][side], 28)
                        self.assertEqual(config['parameter_bytes'][side], 0)
                    else:
                        self.assertEqual(config['table_bytes'][side], 0)
                        self.assertIn(config['parameter_bytes'][side], (0, 4, 8))
        rvq = [c for c in configs if c['family'] == 'residual_vq']
        self.assertEqual({(c['stages'], c['objective'], c['seed']) for c in rvq},
                         {(s, o, seed) for s in (4, 5) for o in ('uniform', 'attention_weighted')
                          for seed in (4827, 4828, 4829)})
        for config in rvq:
            for side in ('k', 'v'):
                self.assertEqual(tuple(config['sides'][side]['tables'].shape), (config['stages'], 256, 8))
                self.assertEqual(config['table_bytes'][side], config['stages'] * 8192)
                self.assertEqual(config['parameter_bytes'][side], 0)

    def test_codec_and_frozen_attention_have_independent_tail_and_byte_oracles(self):
        original = capture(tokens=33)
        snapshots = {side: original[side].clone() for side in ('k', 'v')}
        config = fixed_config(clip=0.75)
        codec = make_codec(config)
        self.assertTrue(callable(codec))
        for side in ('k', 'v'):
            decoded, payload, scales = codec(original[side], side)
            torch.testing.assert_close(decoded, linear_oracle(original[side], 0.75), rtol=1e-6, atol=1e-6)
            self.assertEqual(payload.numel(), original[side].numel() // 2)
            self.assertEqual(scales.numel(), original[side].numel() // 32)
        clean = attention_oracle(original['q'], original['k'], original['v'], original['scale'])
        for mode, expected_bytes in [('k', 4996), ('v', 4888), ('kv', 1436)]:
            row = compare_frozen([original], config, mode)
            self.assertIsInstance(row, dict)
            k = original['k'] if 'k' not in mode else torch.cat((linear_oracle(original['k'][..., :32, :], .75), original['k'][..., 32:, :]), -2)
            v = original['v'] if 'v' not in mode else linear_oracle(original['v'], .75)
            actual = attention_oracle(original['q'], k, v, original['scale'])
            expected = (actual.double() - clean.double()).square().sum() / clean.double().square().sum()
            self.assertAlmostEqual(row['attention_nmse'], expected.item(), places=7)
            self.assertEqual(row['raw_bytes'], expected_bytes)
            self.assertEqual(row['baseline_fp32_bytes'], 8448)
            self.assertEqual(row['reconstructed_cache_bytes'], 8448)
            self.assertEqual(row['table_bytes'], 0)
            self.assertEqual(row['parameter_bytes'], 8 if mode == 'kv' else 4)
            self.assertEqual(row['raw_tail_bytes'], 128 if 'k' in mode else 0)
            for side in ('k', 'v'):
                torch.testing.assert_close(original[side], snapshots[side], rtol=0, atol=0)


if __name__ == '__main__':
    unittest.main()
