"""Bounded operational wiring for the tested precision campaign helpers."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import precision_campaign
import numpy as np
import torch
from transformers import AutoModelForCausalLM, DynamicCache

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
    clean = clean_teacher_logits(model, ids)
    levels = torch.arange(1, 8, dtype=torch.float32) / 7
    codec = lambda tensor, side: grid_roundtrip(tensor, levels, legacy_linear=True)
    failures = 0
    for layout in ('token', 'channel'):
        for mode in ('k', 'v', 'kv'):
            one, one_cache = evaluate_grouped(model, ids, codec, mode, k_layout=layout, chunk_size=1)
            repeat, repeat_cache = evaluate_grouped(model, ids, codec, mode, k_layout=layout, chunk_size=1)
            error = one.double() - repeat.double()
            maximum = error.abs().max().item()
            relative = (error.norm() / one.double().norm()).item()
            first_block = (repeat[:, :32] - clean[:, :32]).abs().max().item() if mode == 'k' else None
            passed = (torch.isfinite(one).all().item() and torch.isfinite(repeat).all().item()
                      and maximum <= 1e-3 and relative <= 1e-4
                      and (first_block is None or first_block <= 1e-3)
                      and one_cache.storage() == repeat_cache.storage())
            emit('tokenwise_repeatability', chunk_size=1, layout=layout, mode=mode, passed=passed,
                 max_absolute_error=maximum, relative_l2_error=relative,
                 first_raw_block_max_error=first_block, absolute_limit=1e-3, relative_limit=1e-4,
                 storage=repeat_cache.storage())
            failures += not passed
            del one, repeat, one_cache, repeat_cache
    if failures:
        raise AssertionError(f'{failures} actual-model tokenwise repeatability checks failed')


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


def load_captures(directory, index_sha256, manifest):
    index_path = directory / 'captures.json'
    if not index_sha256 or sha(index_path) != index_sha256:
        raise ValueError('capture index identity changed')
    if sha(directory / 'manifest.json') != MANIFEST_SHA:
        raise ValueError('capture manifest identity changed')
    records = json.loads(index_path.read_text())
    expected_ids = [doc['id'] for doc in manifest['documents']]
    if (len(records) != 18 or len({row['document_id'] for row in records}) != 18
            or {row['document_id'] for row in records} != set(expected_ids)):
        raise ValueError('capture index must contain all eighteen frozen documents exactly once')
    captures = {}
    for row in records:
        filename = hashlib.sha256(row['document_id'].encode()).hexdigest() + '.npz'
        if row['filename'] != filename:
            raise ValueError('capture filename differs from its document identity')
        path = directory / filename
        if sha(path) != row['sha256'] or path.stat().st_size != row['bytes']:
            raise ValueError('capture tensor artifact identity changed')
        with np.load(path, allow_pickle=False) as arrays:
            q, k, v = (arrays[name] for name in ('q', 'k', 'v'))
            scales = arrays['scale']
            if (q.ndim != 5 or k.ndim != 5 or k.shape != v.shape or not k.shape[0]
                    or q.shape[:2] != k.shape[:2] or k.shape[1] != 1 or q.shape[-2] != 1
                    or k.shape[-2] != 256 or q.shape[-1] != k.shape[-1]
                    or not k.shape[2] or q.shape[2] % k.shape[2] or scales.shape != (k.shape[0],)
                    or any(x.dtype != np.float32 or not np.isfinite(x).all() for x in (q, k, v))
                    or not np.isfinite(scales).all() or not (scales > 0).all()):
                raise ValueError('capture arrays must be aligned finite FP32 final-query Q/K/V')
            captures[row['document_id']] = [dict(
                q=torch.from_numpy(q[i].copy()).to('cuda:0'),
                k=torch.from_numpy(k[i].copy()).to('cuda:0'),
                v=torch.from_numpy(v[i].copy()).to('cuda:0'), scale=float(scales[i])) for i in range(k.shape[0])]
        emit('capture_loaded', document_id=row['document_id'], layers=len(captures[row['document_id']]))
    return captures, records


def validate_configurations(configs):
    if (not isinstance(configs, list) or len(configs) != 28
            or len({config['id'] for config in configs}) != 28):
        raise ValueError('frozen configuration cohort must contain twenty-eight unique entries')
    expected = {(family, None, layout, 32, label) for layout in ('token', 'channel')
                for family in ('linear4', 'log4') for label in ('fixed', 'calibrated')}
    expected |= {(family, None, 'token', 0, 'fixed') for family in ('linear4', 'log4')}
    expected |= {('nonuniform4', seed, layout, 32, str(seed))
                 for layout in ('token', 'channel') for seed in (4827, 4828, 4829)}
    actual = set()
    residual = set()
    for config in configs:
        if (set(config['sides']) != {'k', 'v'} or set(config['parameter_bytes']) != {'k', 'v'}
                or set(config['table_bytes']) != {'k', 'v'}):
            raise ValueError('each configuration requires separate K/V specifications and byte ledgers')
        family = config['family']
        if family == 'residual_vq':
            if config['k_layout'] != 'token' or config['tail_tokens'] != 32:
                raise ValueError('residual configuration requires token layout and causal tail')
            residual.add((config['stages'], config['objective'], config['seed']))
        else:
            label = config['id'].rsplit('-', 1)[-1]
            actual.add((family, config['seed'], config['k_layout'], config['tail_tokens'], label))
        for side in ('k', 'v'):
            spec = config['sides'][side]
            table = spec.get('tables') if family == 'residual_vq' else spec.get('levels') if family == 'nonuniform4' else None
            if table is not None:
                expected_shape = (config['stages'], 256, 8) if family == 'residual_vq' else (7,)
                if (not isinstance(table, torch.Tensor) or table.dtype != torch.float32
                        or tuple(table.shape) != expected_shape or not torch.isfinite(table).all()):
                    raise ValueError('frozen tables must have the contracted finite FP32 shape')
            elif family in ('residual_vq', 'nonuniform4'):
                raise ValueError('learned configuration is missing its frozen table')
            table_bytes = table.numel() * table.element_size() if table is not None else 0
            parameter_bytes = (4 if family == 'linear4' else 8) if config['id'].endswith('-calibrated') else 0
            if config['table_bytes'][side] != table_bytes or config['parameter_bytes'][side] != parameter_bytes:
                raise ValueError('frozen decoder byte ledger differs from actual parameters and tables')
    if (actual != expected or residual != {(stages, objective, seed) for stages in (4, 5)
            for objective in ('uniform', 'attention_weighted') for seed in (4827, 4828, 4829)}):
        raise ValueError('frozen configuration cohort is missing a contracted family, layout, rate or seed')


@torch.inference_mode()
def fit(args, manifest):
    if not torch.cuda.is_available():
        raise ValueError('precision fitting requires CUDA device zero')
    captures, records = load_captures(args.captures, args.captures_sha256, manifest)
    emit('fit_started', calibration_ids=[doc['id'] for doc in manifest['documents'] if doc['split'] == 'calibration'])
    configs, receipts = precision_campaign.fit_configurations(manifest['documents'], captures)
    validate_configurations(configs)
    envelope = dict(format_version=1, manifest_sha256=MANIFEST_SHA, model_snapshot=SNAPSHOT,
                    documents=manifest['documents'], captures_sha256=args.captures_sha256,
                    captures=records, source_sha256=sha(__file__),
                    campaign_source_sha256=sha(precision_campaign.__file__), configurations=configs)
    fitted_path = args.out / 'fitted.pt'
    with fitted_path.open('xb') as stream:
        torch.save(envelope, stream)
    fitted_sha256 = sha(fitted_path)
    receipts.update(fitted_sha256=fitted_sha256, manifest_sha256=MANIFEST_SHA,
                    captures_sha256=args.captures_sha256)
    with (args.out / 'fit-receipts.json').open('x') as stream:
        stream.write(json.dumps(receipts, indent=2, allow_nan=False) + '\n')
    (args.out / 'manifest.json').write_bytes((args.inputs / 'manifest.json').read_bytes())
    emit('fit_complete', configurations=len(configs), fitted_sha256=fitted_sha256)
    compare_captures(args, manifest, captures, configs, fitted_sha256)

@torch.inference_mode()
def compare_captures(args, manifest, captures, configs, fitted_sha256):
    if not 0 <= args.config_start < args.config_stop <= len(configs):
        raise ValueError('invalid frozen comparison configuration interval')
    count = 0
    with (args.out / 'results.jsonl').open('x') as stream:
        for config in configs[args.config_start:args.config_stop]:
            for doc in manifest['documents']:
                for mode in ('k', 'v', 'kv'):
                    row = precision_campaign.compare_frozen(captures[doc['id']], config, mode)
                    row.update(event='frozen_comparison', document_id=doc['id'], split=doc['split'],
                               fitted_sha256=fitted_sha256, manifest_sha256=MANIFEST_SHA)
                    stream.write(json.dumps(row, allow_nan=False) + '\n')
                    stream.flush()
                    count += 1
            emit('frozen_configuration_complete', config_id=config['id'], rows=count)
    if count != (args.config_stop - args.config_start) * 54:
        raise AssertionError('frozen comparison matrix is incomplete')
    emit('frozen_complete', config_start=args.config_start, config_stop=args.config_stop, rows=count, results_sha256=sha(args.out / 'results.jsonl'))


@torch.inference_mode()
def clean_teacher_logits(model, ids):
    cache = DynamicCache(config=model.config)
    device = model.get_input_embeddings().weight.device
    pieces = []
    for start in range(ids.shape[1] - 1):
        output = model(input_ids=ids[:, start:start + 1].to(device),
                       past_key_values=cache, use_cache=True)
        pieces.append(output.logits.float().cpu())
    return torch.cat(pieces, dim=1)


@torch.inference_mode()
def teacher(args, manifest):
    from precision_codec import evaluate_grouped
    from teacher_forced_probe import logit_metrics

    configs = load_fitted(args, manifest)['configurations']
    if not 0 <= args.config_start < args.config_stop <= 28:
        raise ValueError('configuration shard must be a nonempty half-open interval within 0..28')
    documents = [doc for doc in manifest['documents'] if doc['split'] == 'evaluation']
    if args.document_index is not None:
        documents = [documents[args.document_index]]
    selected = configs[args.config_start:args.config_stop]
    emit('teacher_shard', document_ids=[doc['id'] for doc in documents],
         config_start=args.config_start, config_stop=args.config_stop,
         configuration_ids=[config['id'] for config in selected], fitted_sha256=args.fitted_sha256)
    model = load_model()
    count = 0
    with (args.out / 'results.jsonl').open('x') as stream:
        for doc in documents:
            ids = torch.tensor([doc['token_ids']], dtype=torch.int64)
            reference = clean_teacher_logits(model, ids)
            emit('teacher_baseline', document_id=doc['id'], positions=reference.shape[1])
            for config in selected:
                metadata = {side: config['parameter_bytes'][side] + config['table_bytes'][side] for side in ('k', 'v')}
                codec = precision_campaign.make_codec(config)
                for mode in ('k', 'v', 'kv'):
                    actual, cache = evaluate_grouped(model, ids, codec, mode, k_layout=config['k_layout'],
                        tail_tokens=config['tail_tokens'], chunk_size=1, table_bytes=metadata)
                    positions = logit_metrics(reference, actual, ids[:, 1:])
                    if len(positions) != 256:
                        raise AssertionError('teacher track must retain all 256 target positions')
                    reference_total = variant_total = 0.0
                    for position in positions:
                        reference_total += position['reference_target_nll']
                        variant_total += position['variant_target_nll']
                        position.update(cumulative_reference_target_nll=reference_total,
                                        cumulative_variant_target_nll=variant_total,
                                        cumulative_nll_increase=variant_total - reference_total)
                    summary = {name: math.fsum(position[name] for position in positions) / 256 for name in
                               ('kl_reference_to_variant', 'logit_rmse', 'nll_increase', 'top_token_agreement')}
                    summary.update(cumulative_reference_target_nll=reference_total, cumulative_variant_target_nll=variant_total,
                                   cumulative_nll_increase=variant_total - reference_total)
                    storage = cache.storage()
                    storage['parameter_bytes'] = sum(config['parameter_bytes'][side] for side in ('k', 'v') if side in mode)
                    storage['table_bytes'] = sum(config['table_bytes'][side] for side in ('k', 'v') if side in mode)
                    row = dict(event='teacher_track', chunk_size=1, document_id=doc['id'], split=doc['split'],
                               config_id=config['id'], mode=mode, fitted_sha256=args.fitted_sha256,
                               manifest_sha256=MANIFEST_SHA, positions=positions, summary=summary, **storage)
                    stream.write(json.dumps(row, allow_nan=False) + '\n')
                    stream.flush()
                    count += 1
                    emit('teacher_track_complete', document_id=doc['id'], config_id=config['id'], mode=mode,
                         tracks=count, summary=summary)
                    del actual, cache
    expected = len(documents) * len(selected) * 3
    if count != expected:
        raise AssertionError('teacher shard is incomplete')
    emit('teacher_complete', tracks=count, position_records=count * 256,
         results_sha256=sha(args.out / 'results.jsonl'))


def load_fitted(args, manifest):
    if not args.fitted_sha256 or sha(args.fitted) != args.fitted_sha256:
        raise ValueError('fitted artifact identity changed')
    envelope = torch.load(args.fitted, map_location='cpu', weights_only=True)
    if (envelope['format_version'] != 1 or envelope['manifest_sha256'] != MANIFEST_SHA
            or envelope['model_snapshot'] != SNAPSHOT or envelope['documents'] != manifest['documents']
            or len(envelope['captures']) != 18
            or {row['document_id'] for row in envelope['captures']} != {doc['id'] for doc in manifest['documents']}):
        raise ValueError('fitted artifact does not match the complete frozen input cohort')
    configs = envelope['configurations']
    validate_configurations(configs)
    if envelope['campaign_source_sha256'] != sha(precision_campaign.__file__):
        raise ValueError('campaign implementation changed since fitting')
    return envelope


@torch.inference_mode()
def frozen(args, manifest):
    envelope = load_fitted(args, manifest)
    if args.captures_sha256 != envelope['captures_sha256']:
        raise ValueError('frozen captures differ from fitting inputs')
    captures, records = load_captures(args.captures, args.captures_sha256, manifest)
    if records != envelope['captures']:
        raise ValueError('capture records differ from fitting inputs')
    compare_captures(args, manifest, captures, envelope['configurations'], args.fitted_sha256)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['capture', 'fit', 'frozen', 'teacher'])
    parser.add_argument('--inputs', type=Path, default=Path('/input'))
    parser.add_argument('--out', type=Path, default=Path('/out'))
    parser.add_argument('--captures', type=Path, default=Path('/input'))
    parser.add_argument('--captures-sha256')
    parser.add_argument('--fitted', type=Path, default=Path('/input/fitted.pt'))
    parser.add_argument('--fitted-sha256')
    parser.add_argument('--document-index', type=int, choices=range(6))
    parser.add_argument('--config-start', type=int, default=0)
    parser.add_argument('--config-stop', type=int, default=28)
    args = parser.parse_args()
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    manifest = load_manifest(args.inputs)
    emit('start', phase=args.phase, teacher_chunk_size=1, source_sha256=sha(__file__), manifest_sha256=MANIFEST_SHA,
         torch_version=torch.__version__, cuda_version=torch.version.cuda, tf32=False)
    try:
        {'capture': capture, 'fit': fit, 'frozen': frozen, 'teacher': teacher}[args.phase](args, manifest)
    except Exception as error:
        emit('failure', phase=args.phase, error_type=type(error).__name__, error=str(error))
        raise
    if sha(args.inputs / 'manifest.json') != MANIFEST_SHA:
        raise AssertionError('input manifest changed during the trial')


if __name__ == '__main__':
    main()
