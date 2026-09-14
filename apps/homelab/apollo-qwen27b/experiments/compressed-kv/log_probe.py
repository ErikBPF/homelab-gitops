"""Matched-byte linear/logarithmic scalar grids on retained Cartesian captures."""

import argparse
import hashlib
import json
from pathlib import Path

from dictionary_probe import _attention, _nmse
from scalar_probe import scalar_roundtrip
import numpy as np
import torch


def log_roundtrip(tensor, bits):
    if type(bits) is not int or bits not in (2, 4, 8):
        raise ValueError('bits must be 2, 4 or 8')
    if (not isinstance(tensor, torch.Tensor) or tensor.device.type != 'cpu'
            or tensor.dtype != torch.float32 or tensor.ndim == 0
            or not tensor.numel() or tensor.shape[-1] % 32
            or not torch.isfinite(tensor).all()):
        raise ValueError('input must be finite CPU FP32 with groups of 32')
    groups = tensor.reshape(-1, 32)
    maximum = (1 << (bits - 1)) - 1
    peak = groups.abs().amax(dim=1, keepdim=True)
    scales = torch.where(peak == 0, torch.ones_like(peak), peak / maximum)
    if not torch.isfinite(scales).all() or (scales <= 0).any():
        raise ValueError('nonfinite or zero logarithmic scale')
    if bits == 2:
        return scalar_roundtrip(tensor, bits)
    positive = torch.arange(1, maximum + 1, dtype=torch.float32)
    levels = torch.cat((torch.zeros(1), maximum * torch.exp2(-8 * (maximum - positive) / (maximum - 1))))
    physical = scales * levels[None, :]
    if not torch.isfinite(physical).all():
        raise ValueError('nonfinite logarithmic levels')
    magnitude = groups.abs().contiguous()
    upper = torch.searchsorted(physical, magnitude).clamp_max(maximum)
    lower = (upper - 1).clamp_min(0)
    upper_error = (magnitude - physical.gather(1, upper)).abs()
    lower_error = (magnitude - physical.gather(1, lower)).abs()
    nearest = torch.where(upper_error < lower_error, upper, lower)
    signed = (groups.sign() * nearest).to(torch.int32)
    shifts = torch.arange(8 // bits, dtype=torch.int32) * bits
    fields = (signed.flatten() & ((1 << bits) - 1)).reshape(-1, 8 // bits)
    packed = (fields << shifts).sum(dim=1).to(torch.uint8)
    decoded = (packed.to(torch.int32)[:, None] >> shifts) & ((1 << bits) - 1)
    decoded = torch.where(decoded >= 1 << (bits - 1), decoded - (1 << bits), decoded).reshape(-1, 32)
    restored = (physical.gather(1, decoded.abs().long()) * decoded.sign()).reshape(tensor.shape)
    if not torch.isfinite(restored).all():
        raise ValueError('nonfinite logarithmic reconstruction')
    return restored, packed, scales


def comparison(captures, bits, mode, logarithmic=False):
    if (mode not in ('k', 'v', 'kv') or type(logarithmic) is not bool
            or not (type(bits) is int and bits in (2, 4, 8) or bits == 'bf16')
            or bits == 'bf16' and logarithmic):
        raise ValueError('invalid scalar comparison configuration')
    errors = dict(k=0., v=0., attention=0.)
    energies = errors.copy()
    payload = scales = untouched = baseline = 0
    for capture in captures:
        decoded = {}
        for side in ('k', 'v'):
            original = capture[side]
            baseline += original.numel() * original.element_size()
            if side not in mode:
                restored = original
                untouched += original.numel() * original.element_size()
            elif bits == 'bf16':
                encoded = original.bfloat16()
                restored = encoded.float()
                payload += encoded.numel() * encoded.element_size()
            else:
                restored, encoded, scale = (log_roundtrip if logarithmic else scalar_roundtrip)(original, bits)
                payload += encoded.numel() * encoded.element_size()
                scales += scale.numel() * scale.element_size()
            decoded[side] = restored
            errors[side] += (original.double() - restored.double()).square().sum().item()
            energies[side] += original.double().square().sum().item()
        reference = _attention(capture['q'], capture['k'], capture['v'], capture['scale'])
        actual = _attention(capture['q'], decoded['k'], decoded['v'], capture['scale'])
        errors['attention'] += (reference.double() - actual.double()).square().sum().item()
        energies['attention'] += reference.double().square().sum().item()
    result = {**{name + '_nmse': _nmse(errors[name], energies[name]) for name in errors},
              'baseline_fp32_bytes': baseline, 'payload_bytes': payload, 'scale_bytes': scales,
              'untouched_fp32_bytes': untouched, 'raw_bytes': payload + scales + untouched,
              'table_bytes': 0, 'table_padding_bytes': 0}
    json.dumps(result, allow_nan=False)
    return result


def load_inputs(input_dir, cartesian_dir):
    input_dir, cartesian_dir = Path(input_dir), Path(cartesian_dir)
    input_hashes = {}

    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def bundle(directory, count):
        for name in ('results.jsonl', 'source-provenance.json'):
            path = directory / name
            input_hashes[path] = sha(path)
        rows = [json.loads(line) for line in (directory / 'results.jsonl').read_text().splitlines() if line.strip()]
        receipts = {}
        for row in rows:
            for receipt in ([row['artifact']] if 'artifact' in row else []) + list(row.get('samples', {}).values()):
                name = receipt['file']
                if Path(name).name != name or (name in receipts and receipt != receipts[name]):
                    raise ValueError('invalid retained artifact identity')
                receipts[name] = receipt
        if len(receipts) != count or {p.name for p in (directory / 'out').glob('*.npz')} != set(receipts):
            raise ValueError('retained NPZ artifact count differs')
        for name, receipt in receipts.items():
            path = directory / 'out' / name
            if path.stat().st_size != receipt['bytes'] or sha(path) != receipt['sha256']:
                raise ValueError('retained artifact size/hash mismatch: ' + name)
            input_hashes[path] = receipt['sha256']
        return rows

    dictionary_rows, cartesian_rows = bundle(input_dir, 23), bundle(cartesian_dir, 6)
    dictionary_start = next(row for row in dictionary_rows if row['event'] == 'start')
    cartesian_start = next(row for row in cartesian_rows if row['event'] == 'start')
    source = Path(__file__).parent
    kernels = {source / 'dictionary_probe.py': dictionary_start['source_sha256'],
               source / 'scalar_probe.py': dictionary_start['scalar_source_sha256'],
               source / 'cartesian_probe.py': cartesian_start['source_sha256']}
    if any(sha(path) != digest for path, digest in kernels.items()):
        raise ValueError('shared kernel identity differs from retained runs')
    input_hashes.update(kernels)
    input_hashes[Path(__file__)] = sha(Path(__file__))
    documents = [row for row in dictionary_rows if row['event'] == 'capture']
    captures = {}
    for row in documents:
        with np.load(input_dir / 'out' / row['artifact']['file'], allow_pickle=False) as arrays:
            captures[row['id']] = [{**{side: torch.from_numpy(arrays[f'layer_{i}_{side}'].copy())
                                      for side in ('q', 'k', 'v')}, 'scale': float(scale)}
                                    for i, scale in enumerate(arrays['scale'])]
    tables = {}
    for seed in (4827, 4828, 4829):
        tables[seed] = {}
        for side in ('k', 'v'):
            with np.load(cartesian_dir / 'out' / f'cartesian-{seed}-{side}.npz', allow_pickle=False) as arrays:
                tables[seed][side] = {kind: torch.from_numpy(arrays[kind].copy())
                                      for kind in ('vq', 'segments', 'joint_segments')}
    return dict(documents=documents, captures=captures, tables=tables, input_hashes=input_hashes,
                dictionary_rows=dictionary_rows, cartesian_rows=cartesian_rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=Path('/input'))
    parser.add_argument('--cartesian-dir', type=Path, default=Path('/cartesian'))
    args = parser.parse_args()
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    data = load_inputs(args.input_dir, args.cartesian_dir)

    def emit(event, **record):
        print(json.dumps({'event': event, **record}, allow_nan=False), flush=True)

    emit('start', scope='previously_viewed_frozen_q_matched_byte_log_grid', octaves=8,
         dictionary_bundle_sha256='73465d6f361dff338674c1a9d9a293778918f846fabdcfc1bf0d8cbb3cbdc889',
         cartesian_bundle_sha256='a6966f9bc5951acbbb8aaea7ab6bc237e7651b59734c12b9a9f290a6f57d7e04',
         source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         torch_version=torch.__version__, payload_scale_bytes_per_eight={'2': 3, '4': 5, '8': 9},
         table_padding_bytes=0, attention_arithmetic='original_cartesian_fp32', fitting=False)
    retained = {(row['document_id'], row['family'], row['mode']): row for row in data['cartesian_rows']
                if row['event'] == 'comparison' and row['seed'] is None}
    fields = ('k_nmse', 'v_nmse', 'attention_nmse', 'baseline_fp32_bytes', 'payload_bytes',
              'scale_bytes', 'untouched_fp32_bytes', 'raw_bytes', 'table_bytes')
    seen = set()
    linear_checks = log2_checks = 0
    for doc in data['documents']:
        if doc['split'] == 'calibration':
            continue
        for mode in ('k', 'v', 'kv'):
            linear = {}
            for logarithmic, bits in [(False, 2), (False, 4), (False, 8), (False, 'bf16'),
                                      (True, 2), (True, 4), (True, 8)]:
                metrics = comparison(data['captures'][doc['id']], bits, mode, logarithmic)
                family = 'bf16' if bits == 'bf16' else ('log' if logarithmic else 'linear') + str(bits)
                identity = (doc['id'], family, mode)
                if identity in seen:
                    raise ValueError('duplicate logarithmic experiment row')
                seen.add(identity)
                if not logarithmic:
                    original = retained[doc['id'], 'bf16' if bits == 'bf16' else 'scalar' + str(bits), mode]
                    if any(metrics[key] != original[key] for key in fields):
                        raise AssertionError('linear/BF16 replay differs: ' + str(identity))
                    linear[bits] = metrics
                    linear_checks += 1
                elif bits == 2:
                    if metrics != linear[2]:
                        raise AssertionError('log2 differs from linear2: ' + str(identity))
                    log2_checks += 1
                emit('comparison', document_id=doc['id'], split=doc['split'], family=family,
                     bits=bits, mode=mode, **metrics)
    if (len(seen), linear_checks, log2_checks) != (252, 144, 36):
        raise AssertionError('incomplete logarithmic experiment ledger')
    if any(hashlib.sha256(path.read_bytes()).hexdigest() != digest for path, digest in data['input_hashes'].items()):
        raise AssertionError('input artifact or source changed during logarithmic experiment')
    emit('complete', comparisons=len(seen), linear_bf16_exact_checks=linear_checks,
         log2_exact_checks=log2_checks, input_hashes_unchanged=True)
