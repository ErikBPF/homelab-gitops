"""Compare stock Quanto KV caches with the existing Cartesian Q2–Q6 controls."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path

from precision_codec import CausalGroupedCache, grid_roundtrip
from teacher_forced_probe import logit_metrics
import torch
import transformers
from transformers import AutoModelForCausalLM, DynamicCache, QuantizedCache

ARMS = ('stock-default', 'stock-g32-r32', 'own-token', 'own-channel')
PINS = {'qwen': '1cfa9a7208912126459214e8b04321603b3df60c',
        'granite': '707f574c62054322f6b5b04b6d075f0a8f05e0f0'}


def linear_roundtrip(tensor, bits):
    """Symmetric group32 quantization; decode packed bytes before reconstruction."""
    if type(bits) is not int or not 2 <= bits <= 6:
        raise ValueError('precision must be an integer from two through six')
    if bits == 4:
        return grid_roundtrip(tensor, torch.arange(1, 8, dtype=torch.float32) / 7, legacy_linear=True)
    if (not isinstance(tensor, torch.Tensor) or tensor.device.type not in ('cpu', 'cuda')
            or tensor.dtype != torch.float32 or tensor.ndim == 0 or not tensor.numel()
            or tensor.shape[-1] % 32 or not torch.isfinite(tensor).all()):
        raise ValueError('linear input must be finite FP32 CPU/CUDA groups of 32')
    limit = (1 << (bits - 1)) - 1
    groups = tensor.reshape(-1, 32)
    peaks = groups.abs().amax(dim=1, keepdim=True)
    scales = torch.where(peaks == 0, torch.ones_like(peaks), peaks / limit)
    if not torch.isfinite(scales).all() or (scales <= 0).any():
        raise ValueError('nonfinite or zero linear scale')
    signed = (groups / scales).round().clamp(-limit, limit).to(torch.int32)
    fields = (signed.flatten() & ((1 << bits) - 1)).reshape(-1, 8)
    shifts = torch.arange(8, device=tensor.device)
    planes = torch.arange(bits, device=tensor.device)
    packed = ((((fields[:, None, :] >> planes[None, :, None]) & 1)
               << shifts).sum(-1)).to(torch.uint8)
    decoded = ((((packed.to(torch.int32)[:, :, None] >> shifts) & 1)
                << planes[None, :, None]).sum(1))
    decoded = torch.where(decoded > limit, decoded - (1 << bits), decoded).reshape(-1, 32)
    restored = decoded * scales
    if not torch.isfinite(restored).all():
        raise ValueError('nonfinite linear reconstruction')
    return restored.reshape(tensor.shape), packed.flatten(), scales


def make_cache(config, arm, bits=4):
    if type(bits) is not int or not 2 <= bits <= 6:
        raise ValueError('precision must be an integer from two through six')
    if arm == 'clean':
        return DynamicCache(config=config)
    if arm in ARMS[:2]:
        if bits not in (2, 4):
            raise ValueError('Quanto KV supports only two or four bits')
        settings = {} if arm == 'stock-default' else dict(q_group_size=32, residual_length=32)
        return QuantizedCache(backend='quanto', config=config, nbits=bits, **settings)
    if arm in ARMS[2:]:
        return CausalGroupedCache(config, lambda tensor, side: linear_roundtrip(tensor, bits), 'kv', k_layout=arm.removeprefix('own-'), tail_tokens=32)
    raise ValueError('unknown cache variant')


def tensor_bytes(value):
    """Count backing storage, unwrapping Quanto tensors before counting leaves."""
    seen = set()

    def count(item):
        if isinstance(item, (list, tuple)):
            return sum(count(x) for x in item)
        if item is None:
            return 0
        if not isinstance(item, torch.Tensor):
            raise TypeError('cache storage contains a non-tensor')
        flatten = getattr(item, '__tensor_flatten__', None)
        if flatten is not None:
            names, _ = flatten()
            return sum(count(getattr(item, name)) for name in names)
        storage = item.untyped_storage()
        key = (str(item.device), storage.data_ptr())
        if key in seen:
            return 0
        seen.add(key)
        return storage.nbytes()

    return count(value)


@contextmanager
def decoder_devices(model):
    """Select the device required by Quanto's unguarded native unpack launch."""
    layers = [layer for layer in model.model.layers
              if next(layer.parameters()).device.type == 'cuda']
    if not layers:
        yield
        return
    previous = torch.cuda.current_device()

    def select(module, inputs):
        device = next(module.parameters()).device
        if torch.cuda.current_stream(device) != torch.cuda.default_stream(device):
            raise ValueError('Quanto native unpack requires default CUDA streams')
        torch.cuda.set_device(device)

    handles = [layer.register_forward_pre_hook(select, prepend=True) for layer in layers]
    try:
        yield
    finally:
        for handle in handles:
            handle.remove()
        torch.cuda.set_device(previous)


@torch.inference_mode()
def evaluate(model, ids, arm, bits=4):
    if ids.dtype != torch.int64 or ids.ndim != 2 or ids.shape[0] != 1 or ids.shape[1] < 2:
        raise ValueError('expected one integer sequence with next-token targets')
    cache = make_cache(model.config, arm, bits)
    ids = ids.to(model.get_input_embeddings().weight.device)
    pieces, samples = [], []
    with decoder_devices(model):
        for position in range(ids.shape[1] - 1):
            output = model(input_ids=ids[:, position:position + 1], past_key_values=cache, use_cache=True)
            pieces.append(output.logits.float().cpu())
            fields = [getattr(layer, name, None) for layer in cache.layers
                      for name in ('keys', 'values', '_quantized_keys', '_quantized_values')]
            samples.append(tensor_bytes(fields))
    storage = dict(end_resident_bytes=samples[-1], maximum_sampled_resident_bytes=max(samples),
                   resident_bytes_by_position=samples,
                   encoded_ledger=cache.storage() if isinstance(cache, CausalGroupedCache) else None)
    return torch.cat(pieces, dim=1), storage


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields), allow_nan=False), flush=True)


def model_placement(model):
    parameters = list(model.parameters())
    mapping = getattr(model, 'hf_device_map', None)
    if (not parameters or any(p.dtype != torch.float32 or p.device.type != 'cuda' for p in parameters)
            or mapping is not None and set(str(x) for x in mapping.values()) - {'0', '1', 'cuda:0', 'cuda:1'}):
        raise ValueError('non-FP32 weights or CPU/disk offload')
    return dict(parameter_devices=sorted({str(p.device) for p in parameters}),
                device_map={k: str(v) for k, v in mapping.items()} if mapping is not None else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=PINS, required=True)
    parser.add_argument('--model-path', required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--document-index', type=int, choices=range(6), required=True)
    parser.add_argument('--out', type=Path, default=Path('/out'))
    parser.add_argument('--bits', type=int, choices=range(2, 7), nargs='+', default=[4])
    args = parser.parse_args()
    if len(set(args.bits)) != len(args.bits):
        parser.error('duplicate precision widths')
    if digest(args.manifest) != args.manifest_sha256:
        raise ValueError('input manifest identity changed')
    manifest = json.loads(args.manifest.read_text())
    if manifest['model_snapshot'] != PINS[args.model]:
        raise ValueError('manifest model revision differs')
    docs = [doc for doc in manifest['documents'] if doc['split'] == 'evaluation']
    if len(docs) != 6 or len({doc['id'] for doc in docs}) != 6:
        raise ValueError('expected six distinct evaluation documents')
    if any(not 2 <= len(doc['token_ids']) <= 257 for doc in docs):
        raise ValueError('document length outside frozen bound')
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    torch.manual_seed(4827)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    expected = ['af808452-3544-e84a-b33a-fc5588ede436', '818843e6-ce30-5d98-a4b4-8d8580aaa049']
    if args.model == 'granite':
        expected = expected[:1]
    actual = [str(torch.cuda.get_device_properties(i).uuid).removeprefix('GPU-')
              for i in range(torch.cuda.device_count())]
    if actual != expected:
        raise ValueError('GPU identities differ from qualified target')
    model = AutoModelForCausalLM.from_pretrained(args.model_path, local_files_only=True,
        trust_remote_code=False, dtype=torch.float32, attn_implementation='eager',
        device_map='balanced' if args.model == 'qwen' else {'': 0},
        max_memory={i: '12GiB' for i in range(len(expected))}).eval()
    placement = model_placement(model)
    from importlib.metadata import version
    emit('start', model=args.model, revision=PINS[args.model], manifest_sha256=args.manifest_sha256,
         source_sha256=digest(__file__), torch=torch.__version__, transformers=transformers.__version__,
         quanto=version('optimum-quanto'), gpu_uuids=actual, tf32=False, chunk_size=1,
         decoder_cuda_device_context=True, bits=args.bits,
         **placement,
         parameter_bytes=sum(p.numel() * p.element_size() for p in model.parameters()))
    doc = docs[args.document_index]
    ids = torch.tensor([doc['token_ids']], dtype=torch.int64)
    reference, reference_storage = evaluate(model, ids, 'clean')
    emit('baseline', document_id=doc['id'], positions=reference.shape[1], storage=reference_storage)
    tracks = [(bits, arm) for bits in args.bits for arm in ARMS
              if bits in (2, 4) or arm.startswith('own-')]
    for bits in args.bits:
        if bits not in (2, 4):
            for arm in ARMS[:2]:
                emit('unsupported', bits=bits, arm=arm, reason='Quanto KV supports only two or four bits')
    with (args.out / 'results.jsonl').open('x') as stream:
        for bits, arm in tracks:
            logits, storage = evaluate(model, ids, arm, bits)
            positions = logit_metrics(reference, logits, ids[:, 1:])
            cumulative = 0.0
            for position in positions:
                cumulative += position['nll_increase']
                position['cumulative_nll_increase'] = cumulative
            summary = {key: math.fsum(p[key] for p in positions) / len(positions) for key in
                       ('kl_reference_to_variant', 'logit_rmse', 'nll_increase', 'top_token_agreement')}
            summary['cumulative_nll_increase'] = cumulative
            row = dict(model=args.model, revision=PINS[args.model], document_id=doc['id'], arm=arm, bits=bits,
                       token_ids=doc['token_ids'], positions=positions, summary=summary,
                       storage=storage, reference_storage=reference_storage)
            stream.write(json.dumps(row, allow_nan=False) + '\n')
            stream.flush()
            emit('track_complete', document_id=doc['id'], arm=arm, bits=bits, summary=summary,
                 end_resident_bytes=storage['end_resident_bytes'])
            del logits
    if digest(args.manifest) != args.manifest_sha256:
        raise AssertionError('manifest changed during execution')
    emit('complete', tracks=len(tracks), positions=len(positions) * len(tracks),
         results_sha256=digest(args.out / 'results.jsonl'))


if __name__ == '__main__':
    main()
