"""Bounded operational wiring for the tested precision campaign helpers."""
import argparse
import hashlib
import json
from pathlib import Path

import precision_campaign
import numpy as np
import torch
from transformers import AutoModelForCausalLM

SNAPSHOT = '1cfa9a7208912126459214e8b04321603b3df60c'
MANIFEST_SHA = 'ba2447e2fc0e92f6c6387451541f3eac1779baf42100478993ab5f2cbf00f58f'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def emit(event, **record):
    print(json.dumps(dict(event=event, **record), allow_nan=False), flush=True)


def load_model():
    if torch.cuda.device_count() != 2:
        raise ValueError('FP32 model execution requires exactly the two qualified GPUs')
    expected = ['af808452-3544-e84a-b33a-fc5588ede436', '818843e6-ce30-5d98-a4b4-8d8580aaa049']
    actual = [str(torch.cuda.get_device_properties(i).uuid).removeprefix('GPU-') for i in range(2)]
    if actual != expected:
        raise ValueError('visible GPU identities differ from the qualified devices')
    model = AutoModelForCausalLM.from_pretrained(
        '/model/snapshots/' + SNAPSHOT, local_files_only=True, dtype=torch.float32,
        attn_implementation='eager', device_map='balanced', max_memory={0: '12GiB', 1: '12GiB'}).eval()
    if set(str(x) for x in model.hf_device_map.values()) - {'0', '1', 'cuda:0', 'cuda:1'}:
        raise ValueError('CPU or disk model offload is outside this precision cohort')
    if any(p.dtype != torch.float32 for p in model.parameters()):
        raise ValueError('all model parameters must remain FP32')
    emit('model', snapshot=SNAPSHOT, device_map={k: str(v) for k, v in model.hf_device_map.items()},
         parameter_bytes=sum(p.numel() * p.element_size() for p in model.parameters()))
    return model


def load_manifest(inputs):
    path = inputs / 'manifest.json'
    if sha(path) != MANIFEST_SHA:
        raise ValueError('fresh manifest identity changed')
    manifest = json.loads(path.read_text())
    docs = manifest['documents']
    if (manifest['model_snapshot'] != SNAPSHOT or len(docs) != 18
            or len({d['id'] for d in docs}) != 18
            or len({tuple(d['token_ids']) for d in docs}) != 18
            or any(len(d['token_ids']) != 257 for d in docs)
            or any(sum(d['split'] == split for d in docs) != 6
                   for split in ('calibration', 'selection', 'evaluation'))):
        raise ValueError('incomplete frozen document matrix')
    return manifest


@torch.inference_mode()
def qualify_cache(model):
    from precision_codec import evaluate_grouped, grid_roundtrip
    ids = torch.arange(66, dtype=torch.int64).reshape(1, -1)
    device = model.get_input_embeddings().weight.device
    clean = model(input_ids=ids[:, :-1].to(device), use_cache=False).logits.cpu()
    levels = torch.arange(1, 8, dtype=torch.float32) / 7
    codec = lambda tensor, side: grid_roundtrip(tensor, levels, legacy_linear=True)
    failures = 0
    for layout in ('token', 'channel'):
        for mode in ('k', 'v', 'kv'):
            one, one_cache = evaluate_grouped(model, ids, codec, mode, k_layout=layout, chunk_size=1)
            chunks, chunk_cache = evaluate_grouped(model, ids, codec, mode, k_layout=layout, chunk_size=32)
            error = one.double() - chunks.double()
            maximum = error.abs().max().item()
            relative = (error.norm() / one.double().norm()).item()
            first_block = (chunks[:, :32] - clean[:, :32]).abs().max().item() if mode == 'k' else None
            passed = (torch.isfinite(one).all().item() and torch.isfinite(chunks).all().item()
                      and maximum <= 1e-3 and relative <= 1e-4
                      and (first_block is None or first_block <= 1e-3)
                      and one_cache.storage() == chunk_cache.storage())
            emit('cache_qualification', layout=layout, mode=mode, passed=passed,
                 max_absolute_error=maximum, relative_l2_error=relative,
                 first_raw_block_max_error=first_block, absolute_limit=1e-3, relative_limit=1e-4,
                 storage=chunk_cache.storage())
            failures += not passed
            del one, chunks, one_cache, chunk_cache
    if failures:
        raise AssertionError(f'{failures} actual-model cache chunk qualifications failed')


@torch.inference_mode()
def capture(args, manifest):
    model = load_model()
    qualify_cache(model)
    device = model.get_input_embeddings().weight.device
    index = []
    for doc in manifest['documents']:
        ids = torch.tensor([doc['token_ids'][:-1]], dtype=torch.int64, device=device)
        layers = precision_campaign.capture_precision(model, ids)
        filename = hashlib.sha256(doc['id'].encode()).hexdigest() + '.npz'
        path = args.out / filename
        np.savez(path, **{name: np.stack([layer[name].numpy() for layer in layers])
                        for name in ('q', 'k', 'v')},
                 scale=np.array([layer['scale'] for layer in layers]),
                 oracle_max_error=np.array([layer['oracle_max_error'] for layer in layers]))
        record = dict(document_id=doc['id'], filename=filename, sha256=sha(path), bytes=path.stat().st_size,
                      oracle_max_error=max(layer['oracle_max_error'] for layer in layers))
        index.append(record)
        emit('capture', **record)
    (args.out / 'captures.json').write_text(json.dumps(index, indent=2) + '\n')
    (args.out / 'manifest.json').write_bytes((args.inputs / 'manifest.json').read_bytes())
    emit('complete', phase='capture', documents=len(index), bytes=sum(r['bytes'] for r in index))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['capture'])
    parser.add_argument('--inputs', type=Path, default=Path('/input'))
    parser.add_argument('--out', type=Path, default=Path('/out'))
    args = parser.parse_args()
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    manifest = load_manifest(args.inputs)
    emit('start', phase=args.phase, source_sha256=sha(__file__), manifest_sha256=MANIFEST_SHA,
         torch_version=torch.__version__, cuda_version=torch.version.cuda, tf32=False)
    capture(args, manifest)
    if sha(args.inputs / 'manifest.json') != MANIFEST_SHA:
        raise AssertionError('input manifest changed during the trial')


if __name__ == '__main__':
    main()
