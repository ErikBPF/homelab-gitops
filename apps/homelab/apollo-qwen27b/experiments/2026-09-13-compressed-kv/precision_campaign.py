"""Fresh Cartesian precision campaign on fixed calibration and evaluation splits."""
import hashlib
import json
import math
import os
import sys

os.environ['USE_TF'] = '0'
os.environ['USE_FLAX'] = '0'
sys.path.append('/host-deps')

import torch
from transformers import Qwen3Config
from transformers.models.qwen3.modeling_qwen3 import apply_rotary_pos_emb, repeat_kv
from dictionary_probe import _attention, _nmse
from precision_codec import CausalGroupedCache, grid_roundtrip, log_levels
from residual_codebook import attention_sensitivity, fit_residual, normalize_weights, roundtrip


def freeze_documents(rows, encode):
    """Freeze only public problem text and tokens before calibration is run."""
    ranked = []
    identities, texts = set(), set()
    for row in rows:
        identity = str(row['platform']) + ':' + str(row['question_id'])
        text = row['question_content']
        if not isinstance(text, str) or identity in identities:
            raise ValueError('documents require unique identities and text content')
        identities.add(identity)
        text_hash = hashlib.sha256(text.encode()).hexdigest()
        if text_hash in texts:
            raise ValueError('duplicate document text')
        texts.add(text_hash)
        ranked.append((hashlib.sha256(('4827:' + identity).encode()).hexdigest(), identity, text, text_hash))
    result, token_hashes = [], set()
    for _, identity, text, text_hash in sorted(ranked):
        tokens = encode(text)
        if not isinstance(tokens, list) or any(type(x) is not int or x < 0 for x in tokens):
            raise ValueError('tokenizer must return nonnegative integer IDs')
        if len(tokens) < 257:
            continue
        tokens = tokens[:257]
        token_hash = hashlib.sha256(json.dumps(tokens, separators=(',', ':')).encode()).hexdigest()
        if token_hash in token_hashes:
            continue
        token_hashes.add(token_hash)
        result.append(dict(id=identity, split=('calibration', 'selection', 'evaluation')[len(result) // 6],
                           token_ids=tokens, text_sha256=text_hash,
                           tokens_sha256=token_hash))
        if len(result) == 18:
            return result
    raise ValueError('need eighteen distinct documents with at least 257 tokens')


def select_candidate(candidates, losses):
    if not candidates or len(candidates) != len(losses):
        raise ValueError('candidate and calibration loss rows must align')
    count = len(losses[0])
    if not count or any(len(row) != count for row in losses):
        raise ValueError('calibration rows must be nonempty and aligned')
    if any(not math.isfinite(float(x)) or x < 0 for row in losses for x in row):
        raise ValueError('calibration losses must be finite and nonnegative')
    means = [math.fsum(float(x) for x in row) / count for row in losses]
    return dict(candidates[min(range(len(means)), key=means.__getitem__)])


def fit_levels(values, seed):
    if (not isinstance(values, torch.Tensor) or values.ndim != 1 or not values.numel()
            or values.numel() > 262144 or values.dtype != torch.float32
            or not torch.isfinite(values).all() or (values < 0).any() or (values > 1).any()
            or type(seed) is not int):
        raise ValueError('fit requires bounded finite normalized calibration values and integer seed')
    values = values.detach().cpu()
    if not values.any():
        return torch.arange(1, 8, dtype=torch.float32) / 7
    generator = torch.Generator().manual_seed(seed)
    levels = (torch.arange(1, 8, dtype=torch.float32) / 7
              + (torch.rand(7, generator=generator) - 0.5) / 14).clamp(1e-6, 1.0)
    for _ in range(20):
        assigned = (values[:, None] - torch.cat((torch.zeros(1), levels))[None, :]).abs().argmin(-1)
        for index in range(7):
            selected = values[assigned == index + 1]
            if selected.numel():
                levels[index] = selected.mean()
        levels = levels.sort().values
    return levels


@torch.inference_mode()
def capture_precision(model, token_ids):
    """Capture up to 256 tokens, preserving device placement and old replay code."""
    if (not isinstance(token_ids, torch.Tensor) or token_ids.dtype != torch.int64
            or token_ids.ndim != 2 or token_ids.shape[0] != 1 or not 1 <= token_ids.shape[1] <= 256):
        raise ValueError('capture requires one unpadded sequence of 1 through 256 tokens')
    queries = [None] * len(model.model.layers)
    rotary, hooks = [], []
    try:
        for index, layer in enumerate(model.model.layers):
            def save_query(module, args, output, index=index):
                queries[index] = output.detach().cpu().clone()
            hooks.append(layer.self_attn.q_norm.register_forward_hook(save_query))

        def save_rotary(module, args, output):
            rotary.append(tuple(x.detach().cpu().clone() for x in output))
        hooks.append(model.model.rotary_emb.register_forward_hook(save_rotary))
        outputs = model(input_ids=token_ids, use_cache=True, output_attentions=True)
    finally:
        for hook in hooks:
            hook.remove()
    if len(rotary) != 1 or any(q is None for q in queries):
        raise ValueError('capture requires actual normalized queries and one rotary output')
    captures = []
    for index, layer in enumerate(model.model.layers):
        attn = layer.self_attn
        device = attn.q_proj.weight.device
        q = queries[index].to(device).transpose(1, 2)
        cos, sin = (x.to(device) for x in rotary[0])
        q, _ = apply_rotary_pos_emb(q, q, cos, sin)
        k, v = outputs.past_key_values.layers[index].keys, outputs.past_key_values.layers[index].values
        if any(x.dtype != torch.float32 for x in (q, k, v)):
            raise ValueError('precision capture requires FP32 Q/K/V')
        # Preserve the actual matmul shape; the final query has no masked future keys.
        oracle = ((q @ repeat_kv(k, attn.num_key_value_groups).transpose(-2, -1)) * attn.scaling).softmax(-1)[:, :, -1:, :]
        expected = outputs.attentions[index][:, :, -1:, :].to(device)
        error = (oracle - expected).abs().max().item()
        if not torch.allclose(oracle, expected, atol=1e-6, rtol=1e-5):
            raise AssertionError(f'layer {index} final-query oracle mismatch: {error}')
        captures.append(dict(q=q[:, :, -1:, :].cpu().clone(), k=k.cpu().clone(), v=v.cpu().clone(),
                             scale=float(attn.scaling), oracle_max_error=error))
    return captures


def _tensor_hash(tensor):
    return hashlib.sha256(tensor.detach().cpu().contiguous().numpy().tobytes()).hexdigest()


@torch.inference_mode()
def sample_calibration(documents, captures_by_id):
    """Sample aligned calibration vectors and normalized scalar groups only."""
    identities = [doc['id'] for doc in documents if doc['split'] == 'calibration']
    if not identities or len(identities) != len(set(identities)):
        raise ValueError('need distinct calibration documents')
    vectors, weights = {side: [] for side in ('k', 'v')}, {side: [] for side in ('k', 'v')}
    groups = {name: [] for name in ('k_token', 'k_channel', 'v')}
    for identity in identities:
        for capture in captures_by_id[identity]:
            q, k, v = (capture[side] for side in ('q', 'k', 'v'))
            key_weights, value_weights = attention_sensitivity(q, k, v, capture['scale'])
            sealed = k.shape[-2] - (1 + (k.shape[-2] - 1) % 32)
            if not sealed or k.shape[-1] % 32:
                raise ValueError('calibration requires a sealed K block and feature groups of 32')
            prefix = k[..., :sealed, :]
            for side, tensor, sensitivity in (('k', prefix, key_weights[..., :sealed, :]),
                                               ('v', v, value_weights)):
                vectors[side].append(tensor.reshape(-1, 8))
                weights[side].append(sensitivity.reshape(-1, 8).mean(-1))
            groups['k_token'].append(prefix.reshape(-1, 32))
            blocks = prefix.reshape(*prefix.shape[:2], -1, 32, prefix.shape[-1])
            groups['k_channel'].append(blocks.transpose(-1, -2).contiguous().reshape(-1, 32))
            groups['v'].append(v.reshape(-1, 32))
    result = dict(calibration_ids=identities, vectors={}, weights={}, fallback={}, groups={},
                  vector_indices={}, group_indices={}, pool_sizes=dict(vectors={}, groups={}),
                  hashes={name: {} for name in ('vectors', 'weights', 'groups', 'vector_indices', 'group_indices')})

    def indices(count):
        return torch.randperm(count, generator=torch.Generator().manual_seed(4827))[:8192]

    for side in ('k', 'v'):
        pool, weight_pool = torch.cat(vectors[side]), torch.cat(weights[side])
        selected = indices(pool.shape[0])
        result['vector_indices'][side] = selected
        result['vectors'][side] = pool[selected.to(pool.device)].contiguous()
        sampled_weights = weight_pool[selected.to(weight_pool.device)]
        normalized, fallback = normalize_weights(sampled_weights)
        result['weights'][side], result['fallback'][side] = normalized, fallback
        result['pool_sizes']['vectors'][side] = pool.shape[0]
    for name, parts in groups.items():
        pool = torch.cat(parts)
        selected = indices(pool.shape[0])
        sampled = pool[selected.to(pool.device)].abs()
        maxima = sampled.amax(-1, keepdim=True)
        result['groups'][name] = sampled / torch.where(maxima == 0, torch.ones_like(maxima), maxima)
        result['group_indices'][name] = selected
        result['pool_sizes']['groups'][name] = pool.shape[0]
    for field, values in result['hashes'].items():
        values.update({name: _tensor_hash(tensor) for name, tensor in result[field].items()})
    return result


@torch.inference_mode()
def fit_configurations(documents, captures_by_id):
    """Freeze all 28 configurations and calibration receipts before evaluation."""
    sampled = sample_calibration(documents, captures_by_id)
    identities = sampled['calibration_ids']
    receipts = dict(calibration_ids=identities, sampling=dict(
        seed=4827, pool_sizes=sampled['pool_sizes'], hashes=sampled['hashes'], fallback=sampled['fallback'],
        vector_indices={side: x.tolist() for side, x in sampled['vector_indices'].items()},
        group_indices={name: x.tolist() for name, x in sampled['group_indices'].items()},
        weights={side: dict(min=x.min().item(), max=x.max().item(), mean=x.double().mean().item())
                 for side, x in sampled['weights'].items()}), scalar_candidates=[], residual_fits=[])
    configs = []

    def scalar(family, layout, tail, label, sides, seed=None, parameter_bytes=0, table_bytes=0):
        return dict(id=f'{layout}-r{tail}-{family}-{label}', family=family, seed=seed,
                    k_layout=layout, tail_tokens=tail, sides=sides,
                    parameter_bytes={side: parameter_bytes for side in ('k', 'v')},
                    table_bytes={side: table_bytes for side in ('k', 'v')})

    def fixed_sides(family):
        return {side: dict(clip=1.0, legacy_linear=family == 'linear4',
                           **({'span': 8} if family == 'log4' else {})) for side in ('k', 'v')}

    for family in ('linear4', 'log4'):
        configs.append(scalar(family, 'token', 0, 'fixed', fixed_sides(family)))
    for layout in ('token', 'channel'):
        for family in ('linear4', 'log4'):
            configs.append(scalar(family, layout, 32, 'fixed', fixed_sides(family)))
            candidates = [dict(clip=clip) for clip in (0.5, 0.75, 0.9, 1.0)]
            if family == 'log4':
                candidates = [dict(clip=clip, span=span) for clip in (0.5, 0.75, 0.9, 1.0)
                              for span in (2, 4, 6, 8, 12)]
            selected_sides = {}
            for side in ('k', 'v'):
                metrics = []
                for candidate in candidates:
                    sides = fixed_sides(family)
                    sides[side].update(candidate)
                    trial = scalar(family, layout, 32, 'candidate', sides,
                                   parameter_bytes=4 if family == 'linear4' else 8)
                    metrics.append([compare_frozen(captures_by_id[identity], trial, side) for identity in identities])
                losses = [[row['attention_nmse'] for row in rows] for rows in metrics]
                selected = select_candidate(candidates, losses)
                selected_sides[side] = dict(selected, legacy_linear=family == 'linear4')
                receipts['scalar_candidates'].append(dict(
                    family=family, k_layout=layout, side=side, document_ids=identities,
                    candidates=candidates, losses=losses, metrics=metrics, selected=selected))
            configs.append(scalar(family, layout, 32, 'calibrated', selected_sides,
                                  parameter_bytes=4 if family == 'linear4' else 8))
        for seed in (4827, 4828, 4829):
            sides = {side: dict(levels=fit_levels(sampled['groups'][f'k_{layout}' if side == 'k' else 'v'].flatten(), seed),
                                clip=1.0, legacy_linear=False) for side in ('k', 'v')}
            configs.append(scalar('nonuniform4', layout, 32, str(seed), sides, seed=seed, table_bytes=28))
    for stages in (4, 5):
        for objective in ('uniform', 'attention_weighted'):
            for seed in (4827, 4828, 4829):
                config = dict(id=f'token-r32-residual-vq-{stages}-{objective}-{seed}', family='residual_vq',
                              seed=seed, stages=stages, objective=objective, k_layout='token', tail_tokens=32,
                              sides={}, parameter_bytes=dict(k=0, v=0),
                              table_bytes={side: stages * 8192 for side in ('k', 'v')})
                for side in ('k', 'v'):
                    fitted = fit_residual(sampled['vectors'][side], seed, stages,
                                          sampled['weights'][side] if objective == 'attention_weighted' else None)
                    config['sides'][side] = dict(tables=fitted['tables'].detach().cpu())
                    receipts['residual_fits'].append(dict(config_id=config['id'], side=side, stages=stages,
                        objective=objective, seed=seed, losses=fitted['losses'],
                        sample_sha256=sampled['hashes']['vectors'][side],
                        weights_sha256=sampled['hashes']['weights'][side] if objective == 'attention_weighted' else None,
                        fallback=sampled['fallback'][side] if objective == 'attention_weighted' else False,
                        table_sha256=_tensor_hash(fitted['tables'])))
                configs.append(config)
    return configs, receipts


def make_codec(config, device=None):
    """Build the selected K/V codec using frozen parameters on its input device."""
    family = config['family']
    parameters = {}
    for side, spec in config['sides'].items():
        if family == 'residual_vq':
            value = spec['tables']
        elif family == 'linear4':
            value = torch.arange(1, 8, dtype=torch.float32) / 7
        elif family == 'log4':
            value = log_levels(spec['span'])
        elif family == 'nonuniform4':
            value = spec['levels']
        else:
            raise ValueError('unknown precision codec family')
        parameters[side] = value.to(device) if device is not None else value
    placed = {}

    def codec(tensor, side):
        key = (side, tensor.device)
        if key not in placed:
            placed[key] = parameters[side].to(tensor.device)
        if family == 'residual_vq':
            return roundtrip(tensor, placed[key])
        spec = config['sides'][side]
        return grid_roundtrip(tensor, placed[key], clip=spec['clip'], legacy_linear=spec['legacy_linear'])

    return codec


@torch.inference_mode()
def compare_frozen(captures, config, mode='kv'):
    """Replay original K/V through the causal cache and measure frozen attention."""
    if not captures:
        raise ValueError('frozen comparison requires captured layers')
    metadata = {side: config['parameter_bytes'][side] + config['table_bytes'][side] for side in ('k', 'v')}
    cache = CausalGroupedCache(Qwen3Config(num_hidden_layers=len(captures),
                               layer_types=['full_attention'] * len(captures)), make_codec(config), mode,
                               k_layout=config['k_layout'], tail_tokens=config['tail_tokens'], table_bytes=metadata)
    errors = {side: 0.0 for side in ('k', 'v', 'attention')}
    energies = errors.copy()
    for index, capture in enumerate(captures):
        q, k, v = (capture[side] for side in ('q', 'k', 'v'))
        for start in range(0, k.shape[-2], 32):
            cache.update(k[..., start:start + 32, :], v[..., start:start + 32, :], index)
        decoded_k, decoded_v = cache.layers[index].keys, cache.layers[index].values
        clean = _attention(q, k, v, capture['scale'])
        decoded = _attention(q, decoded_k, decoded_v, capture['scale'])
        for side, original, restored in (('k', k, decoded_k), ('v', v, decoded_v), ('attention', clean, decoded)):
            if not torch.isfinite(original).all() or not torch.isfinite(restored).all():
                raise ValueError('nonfinite frozen comparison')
            errors[side] += (restored.double() - original.double()).square().sum().item()
            energies[side] += original.double().square().sum().item()
    storage = cache.storage()
    storage['parameter_bytes'] = sum(config['parameter_bytes'][side] for side in ('k', 'v') if side in mode)
    storage['table_bytes'] = sum(config['table_bytes'][side] for side in ('k', 'v') if side in mode)
    return dict(config_id=config['id'], mode=mode, **storage,
                **{side + '_nmse': _nmse(errors[side], energies[side]) for side in errors})
