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
from transformers.models.qwen3.modeling_qwen3 import apply_rotary_pos_emb, repeat_kv


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
    result = []
    for _, identity, text, text_hash in sorted(ranked):
        tokens = encode(text)
        if not isinstance(tokens, list) or any(type(x) is not int or x < 0 for x in tokens):
            raise ValueError('tokenizer must return nonnegative integer IDs')
        if len(tokens) < 257:
            continue
        tokens = tokens[:257]
        result.append(dict(id=identity, split=('calibration', 'selection', 'evaluation')[len(result) // 6],
                           token_ids=tokens, text_sha256=text_hash,
                           tokens_sha256=hashlib.sha256(json.dumps(tokens, separators=(',', ':')).encode()).hexdigest()))
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
    outputs = model(input_ids=token_ids, use_cache=True, output_hidden_states=True, output_attentions=True)
    captures = []
    for index, layer in enumerate(model.model.layers):
        attn = layer.self_attn
        device = attn.q_proj.weight.device
        hidden = layer.input_layernorm(outputs.hidden_states[index][:, -1:, :].to(device))
        q = attn.q_norm(attn.q_proj(hidden).view(1, 1, -1, attn.head_dim)).transpose(1, 2)
        position = torch.tensor([[token_ids.shape[1] - 1]], device=device)
        cos, sin = model.model.rotary_emb(hidden, position)
        q, _ = apply_rotary_pos_emb(q, q, cos, sin)
        k, v = outputs.past_key_values.layers[index].keys, outputs.past_key_values.layers[index].values
        if any(x.dtype != torch.float32 for x in (q, k, v)):
            raise ValueError('precision capture requires FP32 Q/K/V')
        oracle = ((q @ repeat_kv(k, attn.num_key_value_groups).transpose(-2, -1)) * attn.scaling).softmax(-1)
        expected = outputs.attentions[index][:, :, -1:, :].to(device)
        error = (oracle - expected).abs().max().item()
        if not torch.allclose(oracle, expected, atol=1e-6, rtol=1e-5):
            raise AssertionError(f'layer {index} final-query oracle mismatch: {error}')
        captures.append(dict(q=q.cpu().clone(), k=k.cpu().clone(), v=v.cpu().clone(),
                             scale=float(attn.scaling), oracle_max_error=error))
    return captures


def sample_calibration(documents, captures_by_id):
    """Sample aligned calibration vectors and normalized scalar groups only."""
    return None


def fit_configurations(documents, captures_by_id):
    """Freeze all 28 configurations and calibration receipts before evaluation."""
    return None


def make_codec(config, device=None):
    """Build the selected K/V codec using frozen parameters on its input device."""
    return None


def compare_frozen(captures, config, mode='kv'):
    """Replay original K/V through the causal cache and measure frozen attention."""
    return None
