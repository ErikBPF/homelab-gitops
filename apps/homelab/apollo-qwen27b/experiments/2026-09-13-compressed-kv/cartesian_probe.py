"""Linear Cartesian codecs and paired replay on retained, previously viewed captures."""

import argparse
import hashlib
import json
from pathlib import Path

from dictionary_probe import _attention, _nmse, _comparison as historical_comparison
from joint_segment_probe import verify_replay
from scalar_probe import scalar_roundtrip
import numpy as np
import torch


def _valid(tensor, shape):
    if (not isinstance(tensor, torch.Tensor) or tensor.device.type != 'cpu'
            or tensor.dtype != torch.float32 or tuple(tensor.shape) != shape
            or not tensor.numel() or not torch.isfinite(tensor).all()):
        raise ValueError('expected finite CPU FP32 tensor with shape ' + str(shape))


def _vectors(vectors):
    shape = (vectors.shape[0], 8) if isinstance(vectors, torch.Tensor) and vectors.ndim == 2 else (0, 8)
    _valid(vectors, shape)


def _nearest(vectors, words):
    # Direct differences avoid cancellation in squared-norm distance identities.
    indices = []
    for chunk in vectors.split(256):
        distances = (chunk[:, None] - words[None]).square().sum(dim=-1)
        if not torch.isfinite(distances).all():
            raise ValueError('nonfinite Cartesian squared distances')
        indices.append(distances.argmin(dim=1))
    return torch.cat(indices)


def codewords(kind, table):
    shape = {'vq': (256, 8), 'segments': (32, 2, 8)}.get(kind)
    if shape is None:
        raise ValueError('kind must be vq or segments')
    _valid(table, shape)
    if kind == 'vq':
        return table
    alpha = torch.arange(8, dtype=torch.float32)[None, :, None] / 7
    words = ((1 - alpha) * table[:, :1] + alpha * table[:, 1:]).reshape(256, 8)
    _valid(words, (256, 8))
    return words


def roundtrip(vectors, kind, table):
    _vectors(vectors)
    words = codewords(kind, table)
    codes = _nearest(vectors, words).to(torch.uint8)
    return words[codes.long()], codes


def fit_dictionaries(vectors, seed):
    _vectors(vectors)
    if len(vectors) < 256 or type(seed) is not int:
        raise ValueError('fitting needs at least 256 vectors and an integer seed')
    generator = torch.Generator().manual_seed(seed)
    fitted = []
    for count in (256, 64):
        centers = vectors[torch.randperm(len(vectors), generator=generator)[:count]].clone()
        for _ in range(10):
            assigned = _nearest(vectors, centers)
            totals = torch.zeros_like(centers).index_add_(0, assigned, vectors)
            counts = torch.bincount(assigned, minlength=count)[:, None]
            centers = torch.where(counts > 0, totals / counts.clamp_min(1), centers)
        fitted.append(centers)
    remaining, pairs = list(range(64)), []
    while remaining:
        first = remaining.pop(0)
        nearest = (fitted[1][remaining] - fitted[1][first]).square().sum(dim=-1).argmin().item()
        pairs.append(torch.stack((fitted[1][first], fitted[1][remaining.pop(nearest)])))
    result = {'vq': fitted[0], 'segments': torch.stack(pairs)}
    for kind, table in result.items():
        codewords(kind, table)
    return result


def refine_segments(vectors, initial):
    _vectors(vectors)
    codewords('segments', initial)
    table = initial.detach().clone().requires_grad_(True)
    optimizer = torch.optim.Adam([table], lr=0.03)

    def objective():
        words = codewords('segments', table)
        with torch.no_grad():
            assigned = _nearest(vectors, words.detach())
        loss = (vectors - words[assigned]).square().mean()
        if not torch.isfinite(loss):
            raise ValueError('nonfinite Cartesian refinement objective')
        return loss

    loss = objective()
    losses = [loss.item()]
    for _ in range(50):
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        loss = objective()
        losses.append(loss.item())
    return table.detach(), losses


def score_lut(query, segments):
    _valid(query, (8,))
    _valid(segments, (32, 2, 8))
    endpoint_scores = segments @ query
    alpha = torch.arange(8, dtype=torch.float32)[None, :] / 7
    scores = ((1 - alpha) * endpoint_scores[:, :1] + alpha * endpoint_scores[:, 1:]).flatten()
    _valid(scores, (256,))
    return scores


def comparison(captures, family, tables, mode):
    if mode not in ('k', 'v', 'kv') or family not in ('vq', 'segments', 'scalar8', 'bf16'):
        raise ValueError('invalid comparison family or cache mode')
    errors = dict(k=0., v=0., attention=0.)
    energies = errors.copy()
    payload = scales = untouched = baseline = 0
    learned = family in ('vq', 'segments')
    for capture in captures:
        decoded = {}
        for side in ('k', 'v'):
            original = capture[side]
            baseline += original.numel() * original.element_size()
            if side not in mode:
                restored = original
                untouched += original.numel() * original.element_size()
            elif learned:
                restored, codes = roundtrip(original.reshape(-1, 8), family, tables[side])
                restored = restored.reshape(original.shape)
                payload += codes.numel() * codes.element_size()
            elif family == 'scalar8':
                restored, codes, scale = scalar_roundtrip(original, 8)
                payload += codes.numel() * codes.element_size()
                scales += scale.numel() * scale.element_size()
            else:
                encoded = original.to(torch.bfloat16)
                restored = encoded.float()
                payload += encoded.numel() * encoded.element_size()
            decoded[side] = restored
            errors[side] += (original.double() - restored.double()).square().sum().item()
            energies[side] += original.double().square().sum().item()
        reference = _attention(capture['q'], capture['k'], capture['v'], capture['scale'])
        perturbed = _attention(capture['q'], decoded['k'], decoded['v'], capture['scale'])
        errors['attention'] += (reference.double() - perturbed.double()).square().sum().item()
        energies['attention'] += reference.double().square().sum().item()
    table_bytes = sum(tables[side].numel() * tables[side].element_size() for side in mode) if learned else 0
    allocation = len(mode) * 8192 if learned else 0
    result = {**{side + '_nmse': _nmse(errors[side], energies[side]) for side in errors},
              'baseline_fp32_bytes': baseline, 'payload_bytes': payload, 'scale_bytes': scales,
              'table_bytes': table_bytes, 'untouched_fp32_bytes': untouched,
              'raw_bytes': payload + scales + table_bytes + untouched,
              'allocated_table_bytes': allocation, 'table_padding_bytes': allocation - table_bytes,
              'matched_bytes': payload + scales + allocation + untouched,
              'expanded_decode_working_bytes': allocation,
              'rate_control': 'cartesian_1_byte_per_8_values' if learned else 'standalone_higher_rate_control',
              'excludes': ['packet_metadata', 'allocator_overhead']}
    json.dumps(result, allow_nan=False)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=Path('/input'))
    parser.add_argument('--joint-dir', type=Path, default=Path('/joint'))
    parser.add_argument('--output-dir', type=Path, default=Path('/out'))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    source = Path(__file__).parent
    input_hashes, output_hashes = {}, {}

    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def emit(event, **record):
        print(json.dumps({'event': event, **record}, allow_nan=False), flush=True)

    def load_bundle(directory, count):
        for name in ('results.jsonl', 'source-provenance.json'):
            path = directory / name
            input_hashes[path] = sha(path)
        rows = [json.loads(line) for line in (directory / 'results.jsonl').read_text().splitlines() if line.strip()]
        receipts = {}
        for row in rows:
            for receipt in ([row['artifact']] if 'artifact' in row else []) + list(row.get('samples', {}).values()):
                name = receipt['file']
                if Path(name).name != name or (name in receipts and receipt != receipts[name]):
                    raise ValueError('invalid or conflicting retained artifact identity')
                receipts[name] = receipt
        if len(receipts) != count or {p.name for p in (directory / 'out').glob('*.npz')} != set(receipts):
            raise ValueError('retained NPZ artifact count differs')
        for name, receipt in receipts.items():
            path = directory / 'out' / name
            if path.stat().st_size != receipt['bytes'] or sha(path) != receipt['sha256']:
                raise ValueError('retained artifact size/hash mismatch: ' + name)
            input_hashes[path] = receipt['sha256']
        return rows

    rows = load_bundle(args.input_dir, 23)
    joint_rows = load_bundle(args.joint_dir, 6)
    start = next(row for row in rows if row['event'] == 'start')
    joint_start = next(row for row in joint_rows if row['event'] == 'start')
    shared = {source / 'dictionary_probe.py': start['source_sha256'],
              source / 'scalar_probe.py': start['scalar_source_sha256'],
              source / 'dictionary-prompts.json': start['inputs_sha256'],
              source / 'joint_segment_probe.py': joint_start['source_sha256']}
    if any(sha(path) != digest for path, digest in shared.items()):
        raise ValueError('shared source differs from retained runs')
    input_hashes.update(shared)
    input_hashes[Path(__file__)] = sha(Path(__file__))
    expected = [row for row in joint_rows if row['event'] == 'comparison']
    if len(expected) != 396:
        raise ValueError('joint historical ledger must contain 396 comparisons')
    verify_replay([row for row in expected if row['family'] != 'joint_segments'], rows)
    emit('start', scope='previously_viewed_cartesian_paired_replay_frozen_q',
         dictionary_bundle_sha256='73465d6f361dff338674c1a9d9a293778918f846fabdcfc1bf0d8cbb3cbdc889',
         joint_bundle_sha256='638ddc8c6821a74f97b818406fff0f854a74d2af8c568a7c13527d61a20a8f4b',
         source_sha256=sha(Path(__file__)), model_revision=start['model_revision'],
         dictionary_results_sha256=sha(args.input_dir / 'results.jsonl'),
         joint_results_sha256=sha(args.joint_dir / 'results.jsonl'), torch_version=torch.__version__,
         representation='linear_cartesian_without_radius_or_log', seeds=[4827, 4828, 4829],
         lloyd_iterations=10, joint_steps=50, joint_learning_rate=0.03)
    generator = torch.Generator().manual_seed(4827)
    lut_segments = torch.randn(32, 2, 8, generator=generator) * 3
    lut_max_error = 0.0
    for _ in range(2):
        query = torch.randn(8, generator=generator)
        scores = score_lut(query, lut_segments)
        direct = codewords('segments', lut_segments) @ query
        lut_max_error = max(lut_max_error, (scores - direct).abs().max().item())
        if not torch.allclose(scores, direct, atol=1e-5, rtol=1e-5):
            raise AssertionError('Cartesian LUT/direct numerical mismatch')
    emit('lut_correctness', seed=4827, code_count=256, query_cases=2,
         max_absolute_error=lut_max_error, atol=1e-5, rtol=1e-5, allclose=True)
    captures = {}
    for row in rows:
        if row['event'] == 'capture':
            with np.load(args.input_dir / 'out' / row['artifact']['file'], allow_pickle=False) as arrays:
                captures[row['id']] = [{**{side: torch.from_numpy(arrays[f'layer_{i}_{side}'].copy())
                                          for side in ('q', 'k', 'v')}, 'scale': float(scale)}
                                        for i, scale in enumerate(arrays['scale'])]
    samples = {}
    for side in ('k', 'v'):
        with np.load(args.input_dir / 'out' / f'calibration-sample-{side}.npz', allow_pickle=False) as arrays:
            samples[side] = torch.from_numpy(arrays['vectors'].copy())
    historical, cartesian, frozen = {}, {}, {}
    output_bytes = 0
    for seed in (4827, 4828, 4829):
        with np.load(args.input_dir / 'out' / f'fit-{seed}.npz', allow_pickle=False) as arrays:
            historical[seed] = {side: {kind: torch.from_numpy(arrays[side + '_' + kind].copy())
                                      for kind in ('vq', 'segments')} for side in ('k', 'v')}
        cartesian[seed] = {}
        for side in ('k', 'v'):
            with np.load(args.joint_dir / 'out' / f'joint-{seed}-{side}.npz', allow_pickle=False) as arrays:
                historical[seed][side]['joint_segments'] = torch.from_numpy(arrays['segments'].copy())
            tables = fit_dictionaries(samples[side], seed)
            tables['joint_segments'], losses = refine_segments(samples[side], tables['segments'])
            cartesian[seed][side] = tables
            arrays = {kind: table.numpy() for kind, table in tables.items()}
            arrays['losses'] = np.array(losses, dtype=np.float64)
            path = args.output_dir / f'cartesian-{seed}-{side}.npz'
            np.savez(path, **arrays)
            output_bytes += path.stat().st_size
            if output_bytes > 64 * 1024 * 1024:
                raise ValueError('64MiB new table output budget exceeded')
            output_hashes[path] = sha(path)
            hashes = {kind: hashlib.sha256(table.numpy().tobytes()).hexdigest() for kind, table in tables.items()}
            frozen[seed, side] = hashes
            emit('fit', seed=seed, side=side, calibration_rows=len(samples[side]),
                 calibration_sample_sha256=input_hashes[args.input_dir / 'out' / f'calibration-sample-{side}.npz'],
                 losses=losses, initial_objective=losses[0], final_objective=losses[-1],
                 checkpoint='final_iterate', table_content_sha256=hashes,
                 artifact={'file': path.name, 'bytes': path.stat().st_size, 'sha256': output_hashes[path]})
    ledger = []

    def record(row):
        ledger.append(row)
        emit('comparison', **{key: value for key, value in row.items() if key != 'event'})

    for original in expected:
        family, seed, mode = original['family'], original['seed'], original['mode']
        tables = {side: historical[seed][side][family] for side in ('k', 'v')} if seed is not None else None
        row = {key: original[key] for key in ('event', 'document_id', 'split', 'family', 'seed', 'mode')}
        row.update(historical_comparison(captures[row['document_id']],
                                        'segments' if family == 'joint_segments' else family, tables, mode))
        record(row)
    verify_replay(ledger, expected)
    documents = list(dict.fromkeys((row['document_id'], row['split']) for row in expected))
    for document_id, split in documents:
        for seed, fitted in cartesian.items():
            for family in ('vq', 'segments', 'joint_segments'):
                tables = {side: fitted[side][family] for side in ('k', 'v')}
                for mode in ('k', 'v', 'kv'):
                    record(dict(event='comparison', document_id=document_id, split=split,
                                family='cartesian_' + family, seed=seed, mode=mode,
                                **comparison(captures[document_id], 'vq' if family == 'vq' else 'segments', tables, mode)))
        for family in ('scalar8', 'bf16'):
            for mode in ('k', 'v', 'kv'):
                record(dict(event='comparison', document_id=document_id, split=split, family=family,
                            seed=None, mode=mode, **comparison(captures[document_id], family, None, mode)))
    if len(ledger) != 792:
        raise AssertionError('Cartesian replay ledger must contain 792 rows')
    verify_replay(ledger, ledger)
    if any(sha(path) != digest for path, digest in {**input_hashes, **output_hashes}.items()):
        raise AssertionError('source, input or output artifact changed during replay')
    for (seed, side), hashes in frozen.items():
        if any(hashlib.sha256(cartesian[seed][side][kind].numpy().tobytes()).hexdigest() != digest
               for kind, digest in hashes.items()):
            raise AssertionError('actual Cartesian fitting tables changed during replay')
    emit('complete', comparisons=len(ledger), historical_rows=396, cartesian_rows=324, added_control_rows=72,
         historical_verified=True, input_hashes_unchanged=True, fit_hashes_unchanged=True, artifact_bytes=output_bytes)
