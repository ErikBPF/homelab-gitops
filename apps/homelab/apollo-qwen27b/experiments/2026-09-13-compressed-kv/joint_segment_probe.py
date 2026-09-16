"""Joint endpoint fitting on the retained diagnostic captures."""

import argparse
import hashlib
import json
import math
from pathlib import Path

from dictionary_probe import codewords, _directions, _nearest, _comparison
import numpy as np
import torch


def refine_segments(vectors, initial):
    directions, _ = _directions(vectors)
    codewords('segments', initial)
    table = initial.detach().clone().requires_grad_(True)
    optimizer = torch.optim.Adam([table], lr=0.03)

    def objective():
        words = codewords('segments', table)
        with torch.no_grad():
            assigned = _nearest(directions, words.detach())
        loss = (directions - words[assigned]).square().mean()
        if not torch.isfinite(loss):
            raise ValueError('nonfinite refinement objective')
        return loss

    loss = objective()
    losses = [loss.item()]
    for _ in range(50):
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        with torch.no_grad():
            norms = table.norm(dim=-1, keepdim=True)
            if not torch.isfinite(table).all() or (norms <= 1e-8).any():
                raise ValueError('nonfinite or degenerate refined endpoints')
            table.div_(norms)
        loss = objective()
        losses.append(loss.item())
    return table.detach(), losses


def verify_replay(actual, expected):
    def indexed(rows):
        result = {}
        for row in rows:
            if row.get('event') != 'comparison':
                continue
            try:
                identity = tuple(row[key] for key in ('document_id', 'family', 'seed', 'mode'))
            except KeyError as error:
                raise ValueError('missing replay identity') from error
            if identity in result:
                raise ValueError('duplicate replay comparison')
            result[identity] = row
        return result

    actual, expected = indexed(actual), indexed(expected)
    if actual.keys() != expected.keys():
        raise ValueError('replay identities or counts differ')
    for identity, original in expected.items():
        replay = actual[identity]
        if replay.keys() != original.keys():
            raise ValueError('replay fields differ')
        for key, value in original.items():
            candidate = replay[key]
            if key.endswith('_nmse'):
                if (not isinstance(candidate, (float, int)) or not isinstance(value, (float, int))
                        or not math.isfinite(candidate) or not math.isfinite(value)
                        or abs(candidate - value) > 1e-8 + 1e-6 * abs(value)):
                    raise ValueError(f'replay metric differs: {identity} {key}')
            elif type(candidate) is not type(value) or candidate != value:
                raise ValueError(f'replay field differs: {identity} {key}')
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=Path('/input'))
    parser.add_argument('--output-dir', type=Path, default=Path('/out'))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    source = Path(__file__).parent
    bundle_sha256 = '73465d6f361dff338674c1a9d9a293778918f846fabdcfc1bf0d8cbb3cbdc889'

    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def emit(event, **record):
        print(json.dumps({'event': event, **record}, allow_nan=False), flush=True)

    input_hashes = {path: sha(path) for path in (args.input_dir / 'results.jsonl',
                                               args.input_dir / 'source-provenance.json')}
    rows = [json.loads(line) for line in (args.input_dir / 'results.jsonl').read_text().splitlines() if line.strip()]
    start = next(row for row in rows if row['event'] == 'start')
    source_hashes = {source / 'dictionary_probe.py': start['source_sha256'],
                     source / 'scalar_probe.py': start['scalar_source_sha256'],
                     source / 'dictionary-prompts.json': start['inputs_sha256'],
                     Path(__file__): sha(Path(__file__))}
    if any(sha(path) != digest for path, digest in source_hashes.items()):
        raise ValueError('shared source or frozen document identity differs from retained run')
    receipts = {}
    for row in rows:
        for receipt in ([row['artifact']] if 'artifact' in row else []) + list(row.get('samples', {}).values()):
            name = receipt['file']
            if Path(name).name != name or (name in receipts and receipts[name] != receipt):
                raise ValueError('invalid or conflicting retained artifact identity')
            receipts[name] = receipt
    if len(receipts) != 23 or {p.name for p in (args.input_dir / 'out').glob('*.npz')} != set(receipts):
        raise ValueError('retained bundle must contain exactly 23 declared NPZ artifacts')
    for name, receipt in receipts.items():
        path = args.input_dir / 'out' / name
        if path.stat().st_size != receipt['bytes'] or sha(path) != receipt['sha256']:
            raise ValueError(f'retained artifact size/hash mismatch: {name}')
        input_hashes[path] = receipt['sha256']
    expected = [row for row in rows if row['event'] == 'comparison']
    if len(expected) != 288:
        raise ValueError('retained comparison ledger must contain 288 rows')
    emit('start', scope='previously_viewed_paired_replay_frozen_q', original_bundle_sha256=bundle_sha256,
         source_sha256=sha(Path(__file__)), original_model_revision=start['model_revision'],
         input_results_sha256=input_hashes[args.input_dir / 'results.jsonl'],
         input_provenance_sha256=input_hashes[args.input_dir / 'source-provenance.json'],
         steps=50, learning_rate=0.03, optimizer='Adam', objective='mean_squared_unit_direction_error',
         checkpoint='final_iterate', threads=4, torch_version=torch.__version__)
    captured = {}
    for row in rows:
        if row['event'] != 'capture':
            continue
        with np.load(args.input_dir / 'out' / row['artifact']['file'], allow_pickle=False) as arrays:
            captured[row['id']] = [{**{side: torch.from_numpy(arrays[f'layer_{i}_{side}'].copy())
                                      for side in ('q', 'k', 'v')}, 'scale': float(scale)}
                                    for i, scale in enumerate(arrays['scale'])]
    samples = {}
    for side in ('k', 'v'):
        with np.load(args.input_dir / 'out' / f'calibration-sample-{side}.npz', allow_pickle=False) as arrays:
            samples[side] = torch.from_numpy(arrays['vectors'].copy())
    initial = {}
    refined = {}
    frozen = {}
    output_bytes = 0
    for seed in (4827, 4828, 4829):
        with np.load(args.input_dir / 'out' / f'fit-{seed}.npz', allow_pickle=False) as arrays:
            initial[seed] = {side: {kind: torch.from_numpy(arrays[side + '_' + kind].copy())
                                   for kind in ('vq', 'segments')} for side in ('k', 'v')}
        refined[seed] = {}
        for side in ('k', 'v'):
            table, losses = refine_segments(samples[side], initial[seed][side]['segments'])
            refined[seed][side] = table
            digest = hashlib.sha256(table.numpy().tobytes()).hexdigest()
            path = args.output_dir / f'joint-{seed}-{side}.npz'
            np.savez(path, segments=table.numpy(), losses=np.array(losses, dtype=np.float64))
            output_bytes += path.stat().st_size
            if output_bytes > 64 * 1024 * 1024:
                raise ValueError('64MiB new-output budget exceeded')
            frozen[seed, side] = (digest, path, sha(path))
            emit('fit', seed=seed, side=side, steps=50, learning_rate=0.03, losses=losses,
                 initial_objective=losses[0], final_objective=losses[-1], table_content_sha256=digest,
                 sample_sha256=receipts[f'calibration-sample-{side}.npz']['sha256'],
                 initializer_sha256=receipts[f'fit-{seed}.npz']['sha256'],
                 artifact={'file': path.name, 'bytes': path.stat().st_size, 'sha256': sha(path)})
    baseline = []
    for original in expected:
        family, seed, mode = original['family'], original['seed'], original['mode']
        tables = {side: initial[seed][side][family] for side in ('k', 'v')} if seed is not None else None
        row = {key: original[key] for key in ('event', 'document_id', 'split', 'family', 'seed', 'mode')}
        row.update(_comparison(captured[row['document_id']], family, tables, mode))
        baseline.append(row)
        emit('comparison', **{key: value for key, value in row.items() if key != 'event'})
    verify_replay(baseline, expected)
    documents = list(dict.fromkeys((row['document_id'], row['split']) for row in expected))
    joint_rows = 0
    for document_id, split in documents:
        for seed, tables in refined.items():
            for mode in ('k', 'v', 'kv'):
                emit('comparison', document_id=document_id, split=split, family='joint_segments', seed=seed,
                     mode=mode, **_comparison(captured[document_id], 'segments', tables, mode))
                joint_rows += 1
    if any(sha(path) != digest for path, digest in {**input_hashes, **source_hashes}.items()):
        raise AssertionError('retained input or shared source changed during replay')
    for (seed, side), (digest, path, file_digest) in frozen.items():
        if hashlib.sha256(refined[seed][side].numpy().tobytes()).hexdigest() != digest or sha(path) != file_digest:
            raise AssertionError('refined fitting table changed during scoring')
    if joint_rows != 108:
        raise AssertionError('joint comparison ledger does not contain 108 rows')
    emit('complete', baseline_rows=len(baseline), joint_rows=joint_rows, comparisons=len(baseline) + joint_rows,
         baseline_verified=True, input_hashes_unchanged=True, fit_hashes_unchanged=True, artifact_bytes=output_bytes)
