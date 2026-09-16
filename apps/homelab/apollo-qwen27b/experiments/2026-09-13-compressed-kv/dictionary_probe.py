"""Frozen global dictionary diagnostic; see test-contract.md."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sys

os.environ['USE_TF'] = '0'
os.environ['USE_FLAX'] = '0'
sys.path.append('/host-deps')

import numpy as np
import torch
import transformers
from transformers.models.qwen3.modeling_qwen3 import apply_rotary_pos_emb, repeat_kv

from scalar_probe import scalar_roundtrip


def validate_documents(documents):
    if not isinstance(documents, list) or not documents:
        raise ValueError('documents must be a nonempty list')
    seen = {key: set() for key in ('id', 'group', 'text')}
    for doc in documents:
        if not isinstance(doc, dict) or doc.get('split') not in ('calibration', 'selection', 'evaluation'):
            raise ValueError('invalid document split')
        for key in seen:
            value = doc.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'document {key} must be nonempty text')
            value = ' '.join(value.casefold().split()) if key == 'text' else value
            if value in seen[key]:
                raise ValueError(f'duplicate document {key}')
            seen[key].add(value)
    return True


def _directions(vectors):
    if (not isinstance(vectors, torch.Tensor) or vectors.device.type != 'cpu'
            or vectors.dtype != torch.float32 or vectors.ndim != 2
            or vectors.shape[1] != 8 or not vectors.shape[0] or not torch.isfinite(vectors).all()):
        raise ValueError('vectors must be nonempty finite CPU FP32 rows of eight')
    radii = vectors.norm(dim=-1, keepdim=True)
    if not torch.isfinite(radii).all():
        raise ValueError('vector norm exceeds FP32 range')
    directions = vectors / torch.where(radii == 0, torch.ones_like(radii), radii)
    directions[radii[:, 0] == 0, 0] = 1
    return directions, radii


def _nearest(directions, table):
    return torch.cat([(chunk @ table.T).argmax(dim=1) for chunk in directions.split(2048)])


def fit_dictionaries(vectors, seed):
    directions, _ = _directions(vectors)
    if len(directions) < 256 or type(seed) is not int:
        raise ValueError('fitting needs at least 256 vectors and an integer seed')
    generator = torch.Generator().manual_seed(seed)
    fitted = []
    for count in (256, 64):
        centers = directions[torch.randperm(len(directions), generator=generator)[:count]].clone()
        for _ in range(10):
            assigned = _nearest(directions, centers)
            sums = torch.zeros_like(centers).index_add_(0, assigned, directions)
            norms = sums.norm(dim=1, keepdim=True)
            centers = torch.where(norms > 1e-8, sums / norms.clamp_min(1e-8), centers)
        fitted.append(centers)
    remaining = list(range(64))
    pairs = []
    while remaining:
        first = remaining.pop(0)
        nearest = (fitted[1][remaining] @ fitted[1][first]).argmax().item()
        pairs.append(torch.stack((fitted[1][first], fitted[1][remaining.pop(nearest)])))
    return {'vq': fitted[0], 'segments': torch.stack(pairs)}


def codewords(kind, table):
    shape = {'vq': (256, 8), 'segments': (32, 2, 8)}.get(kind)
    if (shape is None or not isinstance(table, torch.Tensor) or tuple(table.shape) != shape
            or table.device.type != 'cpu' or table.dtype != torch.float32
            or not torch.isfinite(table).all()
            or not torch.allclose(table.norm(dim=-1), torch.ones(shape[:-1]), atol=1e-6, rtol=0)):
        raise ValueError('invalid dictionary kind, shape or unit directions')
    if kind == 'vq':
        return table
    alpha = torch.arange(8, dtype=torch.float32)[None, :, None] / 7
    blended = ((1 - alpha) * table[:, :1, :] + alpha * table[:, 1:, :]).reshape(256, 8)
    norms = blended.norm(dim=-1, keepdim=True)
    if (norms <= 1e-8).any():
        raise ValueError('degenerate segment interpolation')
    return blended / norms


def dictionary_roundtrip(vectors, kind, table):
    directions, radii = _directions(vectors)
    expanded = codewords(kind, table)
    indices = _nearest(directions, expanded).to(torch.uint8)
    radii = radii[:, 0].to(torch.bfloat16)
    if not torch.isfinite(radii).all():
        raise ValueError('radius exceeds BF16 range')
    restored = expanded[indices.long()] * radii.float()[:, None]
    return restored, indices, radii


@torch.inference_mode()
def capture_final_qkv(model, token_ids):
    if token_ids.ndim != 2 or token_ids.shape[0] != 1 or not 1 <= token_ids.shape[1] <= 64:
        raise ValueError('capture requires one unpadded sequence of 1 through 64 tokens')
    outputs = model(input_ids=token_ids, use_cache=True,
                    output_hidden_states=True, output_attentions=True)
    position = torch.tensor([[token_ids.shape[1] - 1]], device=token_ids.device)
    captures = []
    for i, layer in enumerate(model.model.layers):
        hidden = layer.input_layernorm(outputs.hidden_states[i][:, -1:, :])
        attn = layer.self_attn
        q = attn.q_norm(attn.q_proj(hidden).view(1, 1, -1, attn.head_dim)).transpose(1, 2)
        cos, sin = model.model.rotary_emb(hidden, position)
        q, _ = apply_rotary_pos_emb(q, q, cos, sin)
        k, v = outputs.past_key_values.layers[i].keys, outputs.past_key_values.layers[i].values
        oracle = ((q @ repeat_kv(k, attn.num_key_value_groups).transpose(-2, -1)) * attn.scaling).softmax(dim=-1)
        expected = outputs.attentions[i][:, :, -1:, :]
        error = (oracle - expected).abs().max().item()
        if not torch.allclose(oracle, expected, atol=1e-6, rtol=1e-5):
            raise AssertionError(f'layer {i} final-query oracle mismatch: {error}')
        captures.append({'q': q.clone(), 'k': k.clone(), 'v': v.clone(),
                         'scale': float(attn.scaling), 'oracle_max_error': error})
    return captures


def _nmse(error, energy):
    if energy == 0:
        if error != 0:
            raise ValueError('nonzero error against zero-energy reference')
        return 0.0
    return error / energy


def _attention(q, k, v, scale):
    groups = q.shape[1] // k.shape[1]
    weights = ((q @ repeat_kv(k, groups).transpose(-2, -1)) * scale).softmax(dim=-1)
    return weights @ repeat_kv(v, groups)


def _comparison(captures, family, tables, mode):
    errors = {side: 0.0 for side in ('k', 'v', 'attention')}
    energies = errors.copy()
    payload = scales = untouched = original_bytes = 0
    for capture in captures:
        reconstructed = {}
        for side in ('k', 'v'):
            original = capture[side]
            original_bytes += original.numel() * original.element_size()
            if side not in mode:
                decoded = original
                untouched += original.numel() * original.element_size()
            elif family.startswith('scalar'):
                decoded, packed, scale = scalar_roundtrip(original, int(family[-1]))
                payload += packed.numel() * packed.element_size()
                scales += scale.numel() * scale.element_size()
            else:
                decoded, packed, radius = dictionary_roundtrip(original.reshape(-1, 8), family, tables[side])
                decoded = decoded.reshape(original.shape)
                payload += packed.numel() * packed.element_size() + radius.numel() * radius.element_size()
            errors[side] += (original.double() - decoded.double()).square().sum().item()
            energies[side] += original.double().square().sum().item()
            reconstructed[side] = decoded
        actual = _attention(capture['q'], capture['k'], capture['v'], capture['scale'])
        perturbed = _attention(capture['q'], reconstructed['k'], reconstructed['v'], capture['scale'])
        errors['attention'] += (actual.double() - perturbed.double()).square().sum().item()
        energies['attention'] += actual.double().square().sum().item()
    sides = len(mode)
    table_bytes = sum(tables[side].numel() * tables[side].element_size() for side in mode) if tables else 0
    padding = sides * 8192 - table_bytes
    return {**{side + '_nmse': _nmse(errors[side], energies[side]) for side in errors},
            'baseline_fp32_bytes': original_bytes, 'payload_bytes': payload, 'scale_bytes': scales,
            'table_bytes': table_bytes, 'untouched_fp32_bytes': untouched,
            'raw_bytes': payload + scales + table_bytes + untouched,
            'table_padding_bytes': padding, 'allocated_table_bytes': sides * 8192,
            'matched_bytes': payload + scales + sides * 8192 + untouched,
            'expanded_decode_working_bytes': sides * 8192 if tables else 0,
            'rate_control': 'higher_rate' if family == 'scalar4' else 'matched_3_bytes_per_8_values',
            'excludes': ['packet_metadata', 'allocator_overhead']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path('/out'))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    torch.manual_seed(4827)
    root = Path(__file__).parent
    prompt_path = root / 'dictionary-prompts.json'
    documents = json.loads(prompt_path.read_text())
    validate_documents(documents)
    revision = '1cfa9a7208912126459214e8b04321603b3df60c'
    snapshot = '/model/snapshots/' + revision

    def emit(event, **record):
        print(json.dumps({'event': event, **record}, allow_nan=False), flush=True)

    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    artifact_bytes = 0

    def save(name, arrays):
        global artifact_bytes
        if artifact_bytes + sum(value.nbytes for value in arrays.values()) + len(arrays) * 2048 > 1024 ** 3:
            raise ValueError('1GiB artifact budget exceeded')
        path = args.output_dir / name
        np.savez(path, **arrays)
        artifact_bytes += path.stat().st_size
        return {'file': path.name, 'bytes': path.stat().st_size, 'sha256': sha(path)}

    emit('start', scope='frozen_global_dictionary_conditional_attention', model='Qwen/Qwen3-4B',
         model_revision=revision, source_sha256=sha(Path(__file__)), scalar_source_sha256=sha(root / 'scalar_probe.py'),
         inputs_sha256=sha(prompt_path), runtime={'python': platform.python_version(),
         'torch': torch.__version__, 'transformers': transformers.__version__, 'host': platform.node()},
         device='cpu', dtype='float32', max_tokens=64, fit_seeds=[4827, 4828, 4829])
    tokenizer = transformers.AutoTokenizer.from_pretrained(snapshot, local_files_only=True, trust_remote_code=False)
    model = transformers.AutoModelForCausalLM.from_pretrained(snapshot, local_files_only=True,
            trust_remote_code=False, dtype=torch.float32, attn_implementation='eager').cpu().eval()
    captured = {}
    for doc in documents:
        ids = tokenizer(doc['text'], return_tensors='pt', truncation=True, max_length=64)['input_ids']
        captures = capture_final_qkv(model, ids)
        captured[doc['id']] = captures
        arrays = {f'layer_{i}_{side}': c[side].numpy() for i, c in enumerate(captures) for side in ('q', 'k', 'v')}
        arrays['scale'] = np.array([c['scale'] for c in captures], dtype=np.float64)
        arrays['token_ids'] = ids.numpy()
        receipt = save(doc['id'] + '.npz', arrays)
        emit('capture', **doc, token_ids=ids[0].tolist(), artifact=receipt,
             shapes=[{side: list(c[side].shape) for side in ('q', 'k', 'v')} for c in captures],
             oracle_max_error=max(c['oracle_max_error'] for c in captures))
    del model
    calibration = [doc for doc in documents if doc['split'] == 'calibration']
    samples = {}
    sample_hashes = {}
    for side in ('k', 'v'):
        pool = torch.cat([c[side].reshape(-1, 8) for doc in calibration for c in captured[doc['id']]])
        indices = torch.randperm(len(pool), generator=torch.Generator().manual_seed(4827))[:8192]
        samples[side] = pool[indices].clone()
        sample_hashes[side] = save(f'calibration-sample-{side}.npz',
                                  {'vectors': samples[side].numpy(), 'pool_indices': indices.numpy()})
        del pool
    fits = {}
    frozen_hashes = {}
    frozen_table_hashes = {}
    for seed in (4827, 4828, 4829):
        fits[seed] = {side: fit_dictionaries(samples[side], seed) for side in ('k', 'v')}
        arrays = {side + '_' + kind: fits[seed][side][kind].numpy() for side in ('k', 'v') for kind in ('vq', 'segments')}
        receipt = save(f'fit-{seed}.npz', arrays)
        frozen_hashes[receipt['file']] = receipt['sha256']
        frozen_table_hashes[seed] = {name: hashlib.sha256(array.tobytes()).hexdigest()
                                     for name, array in arrays.items()}
        emit('fit', seed=seed, model_revision=revision, artifact=receipt, calibration_ids=[d['id'] for d in calibration],
             samples=sample_hashes, sample_rows={side: len(samples[side]) for side in samples},
             table_content_sha256=frozen_table_hashes[seed], iterations=10, pairing='greedy_nearest_fixed_endpoints')
    learned_rows = scalar_rows = 0
    for doc in documents:
        if doc['split'] == 'calibration':
            continue
        for seed, fit in fits.items():
            for kind in ('vq', 'segments'):
                tables = {side: fit[side][kind] for side in ('k', 'v')}
                for mode in ('k', 'v', 'kv'):
                    emit('comparison', document_id=doc['id'], split=doc['split'], family=kind, seed=seed,
                         mode=mode, **_comparison(captured[doc['id']], kind, tables, mode))
                    learned_rows += 1
        for family in ('scalar2', 'scalar4'):
            for mode in ('k', 'v', 'kv'):
                emit('comparison', document_id=doc['id'], split=doc['split'], family=family, seed=None,
                     mode=mode, **_comparison(captured[doc['id']], family, None, mode))
                scalar_rows += 1
    if any(sha(args.output_dir / name) != expected for name, expected in frozen_hashes.items()):
        raise AssertionError('frozen fitting artifact changed during scoring')
    for seed, fit in fits.items():
        for side in ('k', 'v'):
            for kind in ('vq', 'segments'):
                actual = hashlib.sha256(fit[side][kind].numpy().tobytes()).hexdigest()
                if actual != frozen_table_hashes[seed][side + '_' + kind]:
                    raise AssertionError('in-memory fitting table changed during scoring')
    if (learned_rows, scalar_rows) != (216, 72):
        raise AssertionError('comparison ledger does not contain every fixed variant')
    emit('complete', learned_rows=learned_rows, scalar_rows=scalar_rows, comparisons=learned_rows + scalar_rows,
         capture_documents=len(captured), artifact_bytes=artifact_bytes, fit_hashes_unchanged=True)
