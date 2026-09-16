"""Teacher-forced Cartesian cache propagation on retained diagnostic documents."""

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

import torch
import transformers
from transformers import DynamicCache


class QuantizedCache(DynamicCache):
    def __init__(self, config, codec, mode):
        if mode not in ('k', 'v', 'kv') or not callable(codec):
            raise ValueError('cache requires a codec and k, v or kv mode')
        super().__init__(config=config)
        self.codec, self.mode = codec, mode
        self.incoming_fp32_bytes = 0
        self.untouched_fp32_bytes = 0

    def update(self, key_states, value_states, layer_idx, *args, **kwargs):
        decoded = []
        for side, tensor in (('k', key_states), ('v', value_states)):
            size = tensor.numel() * tensor.element_size()
            self.incoming_fp32_bytes += size
            if side in self.mode:
                decoded.append(self.codec(tensor, side))
            else:
                self.untouched_fp32_bytes += size
                decoded.append(tensor)
        return super().update(*decoded, layer_idx, *args, **kwargs)


@torch.inference_mode()
def evaluate_teacher_forced(model, token_ids, codec, mode):
    if (not isinstance(token_ids, torch.Tensor) or token_ids.device.type != 'cpu'
            or token_ids.dtype != torch.int64 or token_ids.ndim != 2
            or token_ids.shape[0] != 1 or token_ids.shape[1] < 2):
        raise ValueError('teacher forcing requires one CPU integer sequence with next-token targets')
    cache = QuantizedCache(model.config, codec, mode)
    logits = model(input_ids=token_ids[:, :-1], past_key_values=cache, use_cache=True).logits
    return logits, cache


def logit_metrics(reference, actual, targets):
    if (not isinstance(reference, torch.Tensor) or not isinstance(actual, torch.Tensor)
            or reference.ndim != 3 or reference.shape[0] != 1
            or not reference.shape[1] or not reference.shape[2]
            or reference.shape != actual.shape
            or reference.device.type != 'cpu' or actual.device.type != 'cpu'
            or not reference.is_floating_point() or not actual.is_floating_point()
            or not torch.isfinite(reference).all() or not torch.isfinite(actual).all()
            or not isinstance(targets, torch.Tensor) or targets.dtype != torch.int64
            or targets.device.type != 'cpu' or targets.shape != reference.shape[:-1]
            or (targets < 0).any() or (targets >= reference.shape[-1]).any()):
        raise ValueError('metrics require aligned finite CPU logits and valid next-token targets')
    original, variant = reference.double(), actual.double()
    original_logp, variant_logp = original.log_softmax(-1), variant.log_softmax(-1)
    kl = (original_logp.exp() * (original_logp - variant_logp)).sum(-1)[0]
    rmse = (original - variant).square().mean(-1).sqrt()[0]
    original_nll = -original_logp.gather(-1, targets[..., None])[0, :, 0]
    variant_nll = -variant_logp.gather(-1, targets[..., None])[0, :, 0]
    agreement = (original.argmax(-1) == variant.argmax(-1))[0]
    records = [dict(position=i, target_token_id=targets[0, i].item(),
                    kl_reference_to_variant=kl[i].item(), logit_rmse=rmse[i].item(),
                    reference_target_nll=original_nll[i].item(),
                    variant_target_nll=variant_nll[i].item(),
                    nll_increase=(variant_nll[i] - original_nll[i]).item(),
                    top_token_agreement=agreement[i].item())
               for i in range(reference.shape[1])]
    json.dumps(records, allow_nan=False)
    return records


@torch.inference_mode()
def main():
    from cartesian_probe import roundtrip as cartesian_roundtrip
    from log_probe import load_inputs, log_roundtrip
    from scalar_probe import scalar_roundtrip

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=Path('/input'))
    parser.add_argument('--cartesian-dir', type=Path, default=Path('/cartesian'))
    parser.add_argument('--document-ids', nargs='+')
    args = parser.parse_args()
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    torch.manual_seed(4827)

    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def emit(event, **record):
        print(json.dumps(dict(event=event, **record), allow_nan=False), flush=True)

    inputs = load_inputs(args.input_dir, args.cartesian_dir)
    all_documents = [doc for doc in inputs['documents'] if doc['split'] == 'evaluation']
    if len(all_documents) != 6:
        raise ValueError('expected the six retained evaluation documents')
    selected = args.document_ids or [doc['id'] for doc in all_documents]
    if len(set(selected)) != len(selected) or not set(selected) <= {doc['id'] for doc in all_documents}:
        raise ValueError('document IDs must be a unique subset of the retained evaluation documents')
    documents = [doc for doc in all_documents if doc['id'] in selected]
    tables = inputs['tables']
    input_hashes = {**inputs['input_hashes'], Path(__file__): sha(Path(__file__))}

    def table_hashes():
        return {f'{seed}:{side}:{kind}': hashlib.sha256(table.numpy().tobytes()).hexdigest()
                for seed, sides in tables.items() for side, kinds in sides.items()
                for kind, table in kinds.items()}

    frozen_tables = table_hashes()
    configurations = [(f'{grid}{bits}', None) for grid in ('linear', 'log') for bits in (2, 4, 8)]
    configurations += [('bf16', None)]
    configurations += [('cartesian_' + kind, seed) for kind in ('vq', 'segments', 'joint_segments')
                       for seed in (4827, 4828, 4829)]
    expected_tracks = len(documents) * len(configurations) * 3
    reference_positions = sum(len(doc['token_ids']) - 1 for doc in documents)
    expected_positions = reference_positions * len(configurations) * 3
    revision = '1cfa9a7208912126459214e8b04321603b3df60c'
    emit('start', scope='previously_viewed_teacher_forced_cartesian_propagation',
         model='Qwen/Qwen3-4B', model_revision=revision, device='cpu', dtype='float32', attention='eager',
         source_sha256=sha(Path(__file__)), input_sha256={str(path): digest for path, digest in input_hashes.items()},
         table_content_sha256=frozen_tables, document_ids=[doc['id'] for doc in documents],
         reference_positions=reference_positions, expected_tracks=expected_tracks,
         expected_variant_positions=expected_positions, log_span_octaves=8,
         runtime={'python': platform.python_version(), 'torch': torch.__version__,
                  'transformers': transformers.__version__, 'host': platform.node()})
    model = transformers.AutoModelForCausalLM.from_pretrained(
        '/model/snapshots/' + revision, local_files_only=True, trust_remote_code=False,
        dtype=torch.float32, attn_implementation='eager').cpu().eval()
    tracks = positions = log2_checks = 0
    for doc in documents:
        token_ids = torch.tensor([doc['token_ids']], dtype=torch.int64)
        targets = token_ids[:, 1:]
        reference = model(input_ids=token_ids[:, :-1], use_cache=False).logits
        clean, clean_cache = evaluate_teacher_forced(model, token_ids, lambda tensor, side: tensor, 'kv')
        baseline_error = (reference - clean).abs().max().item()
        if not torch.allclose(reference, clean, atol=1e-4, rtol=1e-4):
            raise AssertionError(f'clean cached baseline differs for {doc["id"]}: {baseline_error}')
        emit('baseline', document_id=doc['id'], split=doc['split'], token_ids=doc['token_ids'],
             positions=logit_metrics(reference, reference, targets),
             baseline_cached_max_error=baseline_error, baseline_fp32_bytes=clean_cache.incoming_fp32_bytes,
             baseline_bf16_bytes=clean_cache.incoming_fp32_bytes // 2)
        del clean, clean_cache
        linear2 = {}
        for family, seed in configurations:
            for mode in ('k', 'v', 'kv'):
                counts = {'payload_bytes': 0, 'scale_bytes': 0}
                kind = family.removeprefix('cartesian_') if seed is not None else None

                def codec(tensor, side):
                    if family == 'bf16':
                        packed = tensor.to(torch.bfloat16)
                        counts['payload_bytes'] += packed.numel() * packed.element_size()
                        return packed.float()
                    if seed is not None:
                        restored, packed = cartesian_roundtrip(
                            tensor.reshape(-1, 8), 'vq' if kind == 'vq' else 'segments', tables[seed][side][kind])
                        counts['payload_bytes'] += packed.numel() * packed.element_size()
                        return restored.reshape(tensor.shape)
                    bits = int(family.removeprefix('linear').removeprefix('log'))
                    quantize = log_roundtrip if family.startswith('log') else scalar_roundtrip
                    restored, packed, scales = quantize(tensor, bits)
                    counts['payload_bytes'] += packed.numel() * packed.element_size()
                    counts['scale_bytes'] += scales.numel() * scales.element_size()
                    return restored

                logits, cache = evaluate_teacher_forced(model, token_ids, codec, mode)
                metrics = logit_metrics(reference, logits, targets)
                table_bytes = sum(tables[seed][side][kind].numel() * tables[seed][side][kind].element_size()
                                  for side in mode) if seed is not None else 0
                storage = dict(**counts, table_bytes=table_bytes,
                               untouched_fp32_bytes=cache.untouched_fp32_bytes,
                               raw_bytes=sum(counts.values()) + table_bytes + cache.untouched_fp32_bytes,
                               baseline_fp32_bytes=cache.incoming_fp32_bytes,
                               baseline_bf16_bytes=cache.incoming_fp32_bytes // 2,
                               reconstructed_cache_bytes=sum(tensor.numel() * tensor.element_size()
                                                             for layer in cache.layers for tensor in (layer.keys, layer.values)))
                if family == 'linear2':
                    linear2[mode] = (logits.clone(), metrics, storage)
                if family == 'log2':
                    original_logits, original_metrics, original_storage = linear2[mode]
                    if not torch.equal(logits, original_logits) or metrics != original_metrics or storage != original_storage:
                        raise AssertionError(f'log2 differs from linear2 for {doc["id"]}/{mode}')
                    log2_checks += 1
                emit('track', document_id=doc['id'], split=doc['split'], family=family, seed=seed, mode=mode,
                     position_count=len(metrics), positions=metrics, **storage,
                     excludes=['packet_metadata', 'allocator_overhead'],
                     rate_comparison='linear_log_same_bits_equal_bytes; learned_codes_have_distinct_rate')
                tracks += 1
                positions += len(metrics)
                del logits, cache
    if tracks != expected_tracks or positions != expected_positions or log2_checks != len(documents) * 3:
        raise AssertionError('teacher-forced comparison ledger is incomplete')
    if any(sha(path) != digest for path, digest in input_hashes.items()) or table_hashes() != frozen_tables:
        raise AssertionError('retained inputs, source or tables changed during teacher forcing')
    emit('complete', document_ids=[doc['id'] for doc in documents], reference_positions=reference_positions,
         tracks=tracks, variant_positions=positions, expected_tracks=expected_tracks,
         expected_variant_positions=expected_positions, log2_linear2_identical_checks=log2_checks,
         input_hashes_unchanged=True, table_hashes_unchanged=True)


if __name__ == '__main__':
    main()
