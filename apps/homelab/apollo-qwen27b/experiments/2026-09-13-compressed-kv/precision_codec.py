"""Four-bit Cartesian grids and delayed sealing of causal K groups."""

import math
import os
import sys

os.environ['USE_TF'] = '0'
os.environ['USE_FLAX'] = '0'
sys.path.append('/host-deps')

import torch
from transformers import DynamicCache
from transformers.cache_utils import DynamicLayer


def log_levels(span):
    if isinstance(span, bool) or not isinstance(span, (float, int)) or not math.isfinite(span) or span <= 0:
        raise ValueError('log span must be finite and positive')
    levels = torch.exp2(-span * (7 - torch.arange(1, 8, dtype=torch.float32)) / 6)
    if not torch.isfinite(levels).all() or (levels <= 0).any():
        raise ValueError('unsafe logarithmic levels')
    return levels


def grid_roundtrip(tensor, levels, clip=1.0, legacy_linear=False):
    if (not isinstance(tensor, torch.Tensor) or tensor.device.type not in ('cpu', 'cuda')
            or tensor.dtype != torch.float32 or tensor.ndim == 0 or not tensor.numel()
            or tensor.shape[-1] % 32 or not torch.isfinite(tensor).all()):
        raise ValueError('grid input must be finite FP32 CPU/CUDA groups of 32')
    if (not isinstance(levels, torch.Tensor) or levels.device.type not in ('cpu', 'cuda')
            or levels.dtype != torch.float32 or tuple(levels.shape) != (7,)
            or not torch.isfinite(levels).all() or (levels <= 0).any()
            or (levels[1:] < levels[:-1]).any()):
        raise ValueError('grid must have seven finite positive ordered FP32 levels')
    if (isinstance(clip, bool) or not isinstance(clip, (float, int))
            or not math.isfinite(clip) or not 0 < clip <= 1 or type(legacy_linear) is not bool):
        raise ValueError('invalid clipping or linear-grid policy')
    levels = levels.to(tensor.device)
    # CPU and CUDA division can differ by one FP32 rounding step.
    if legacy_linear and not torch.allclose(
            levels, torch.arange(1, 8, device=tensor.device, dtype=torch.float32) / 7,
            rtol=torch.finfo(torch.float32).eps, atol=0):
        raise ValueError('linear policy requires the linear grid')
    groups = tensor.reshape(-1, 32)
    peaks = groups.abs().amax(dim=1, keepdim=True)
    scales = torch.where(peaks == 0, torch.ones_like(peaks), peaks * clip / 7)
    if not torch.isfinite(scales).all() or (scales <= 0).any():
        raise ValueError('nonfinite or zero grid scale')
    physical = scales * torch.cat((levels.new_zeros(1), levels * 7))[None, :]
    if not torch.isfinite(physical).all():
        raise ValueError('nonfinite physical grid levels')
    if legacy_linear:
        scaled = groups / scales
        if not torch.isfinite(scaled).all():
            raise ValueError('nonfinite scaled linear input')
        signed = scaled.round().clamp(-7, 7).to(torch.int32)
    else:
        magnitude = groups.abs().contiguous()
        upper = torch.searchsorted(physical, magnitude).clamp_max(7)
        lower = (upper - 1).clamp_min(0)
        high_error = (magnitude - physical.gather(1, upper)).abs()
        low_error = (magnitude - physical.gather(1, lower)).abs()
        signed = (groups.sign() * torch.where(high_error < low_error, upper, lower)).to(torch.int32)
    fields = (signed.flatten() & 15).reshape(-1, 2)
    packed = (fields[:, 0] | (fields[:, 1] << 4)).to(torch.uint8)
    decoded = torch.stack((packed & 15, packed >> 4), dim=1).to(torch.int32)
    decoded = torch.where(decoded >= 8, decoded - 16, decoded).reshape(-1, 32)
    restored = decoded * scales if legacy_linear else physical.gather(1, decoded.abs().long()) * decoded.sign()
    if not torch.isfinite(restored).all():
        raise ValueError('nonfinite grid reconstruction')
    return restored.reshape(tensor.shape), packed, scales


def grouped_roundtrip(tensor, codec, layout='token'):
    if (not isinstance(tensor, torch.Tensor) or tensor.ndim != 4 or not tensor.numel()
            or tensor.dtype != torch.float32 or tensor.device.type not in ('cpu', 'cuda')
            or layout not in ('token', 'channel') or not callable(codec)):
        raise ValueError('grouping requires FP32 [batch, head, token, feature] input')
    grouped = tensor if layout == 'token' else tensor.transpose(-1, -2).contiguous()
    if grouped.shape[-1] % 32:
        raise ValueError('grouped dimension must be divisible by 32')
    decoded, payload, scales = codec(grouped)
    if (decoded.shape != grouped.shape or decoded.dtype != grouped.dtype or decoded.device != grouped.device
            or payload.dtype != torch.uint8 or scales.dtype != torch.float32
            or payload.device != tensor.device or scales.device != tensor.device
            or not torch.isfinite(decoded).all() or not torch.isfinite(scales).all()):
        raise ValueError('codec returned invalid decoded values or stored fields')
    return decoded if layout == 'token' else decoded.transpose(-1, -2).contiguous(), payload, scales


class CausalGroupedCache(DynamicCache):
    def __init__(self, config, codec, mode, k_layout='token', tail_tokens=32, table_bytes=None):
        if (not callable(codec) or mode not in ('k', 'v', 'kv') or k_layout not in ('token', 'channel')
                or type(tail_tokens) is not int or tail_tokens not in (0, 32)
                or tail_tokens == 0 and k_layout != 'token'):
            raise ValueError('invalid grouped-cache policy')
        super().__init__(config=config)
        if self.offloading or any(type(layer) is not DynamicLayer for layer in self.layers):
            raise ValueError('only ordinary full-attention, non-offloaded cache layers are supported')
        table_bytes = {'k': 0, 'v': 0} if table_bytes is None else dict(table_bytes)
        if (set(table_bytes) != {'k', 'v'}
                or any(type(value) is not int or value < 0 for value in table_bytes.values())):
            raise ValueError('table byte counts must be nonnegative K/V integers')
        self.codec, self.mode, self.k_layout, self.tail_tokens = codec, mode, k_layout, tail_tokens
        self.table_bytes = sum(table_bytes[side] for side in mode)
        self.payload_bytes = self.scale_bytes = self.incoming_bytes = self.untouched_bytes = 0

    def update(self, key_states, value_states, layer_idx, *args, **kwargs):
        if (type(layer_idx) is not int or not 0 <= layer_idx < len(self.layers)
                or key_states.ndim != 4 or key_states.shape != value_states.shape
                or key_states.dtype != torch.float32 or value_states.dtype != torch.float32
                or key_states.device != value_states.device or key_states.device.type not in ('cpu', 'cuda')
                or not key_states.numel()):
            raise ValueError('invalid incoming full-attention FP32 K/V tensors')
        layer = self.layers[layer_idx]
        previous = layer.get_seq_length()
        count = key_states.shape[-2]
        if self.tail_tokens and count > 32 - previous % 32:
            raise ValueError('incoming chunk crosses a 32-token sealing boundary')
        fields = []
        sealed = None
        if 'k' in self.mode:
            if self.tail_tokens == 0:
                key_states, payload, scales = grouped_roundtrip(key_states, lambda x: self.codec(x, 'k'))
                fields.append((payload, scales))
            elif previous and previous % 32 == 0:
                decoded, payload, scales = grouped_roundtrip(
                    layer.keys[..., -32:, :], lambda x: self.codec(x, 'k'), self.k_layout)
                sealed = torch.cat((layer.keys[..., :-32, :], decoded), dim=-2)
                fields.append((payload, scales))
        if 'v' in self.mode:
            value_states, payload, scales = grouped_roundtrip(value_states, lambda x: self.codec(x, 'v'))
            fields.append((payload, scales))
        if sealed is not None:
            layer.keys = sealed
        result = super().update(key_states, value_states, layer_idx, *args, **kwargs)
        self.incoming_bytes += (key_states.numel() + value_states.numel()) * 4
        self.untouched_bytes += sum(tensor.numel() * 4 for side, tensor in (('k', key_states), ('v', value_states))
                                   if side not in self.mode)
        self.payload_bytes += sum(payload.numel() * payload.element_size() for payload, _ in fields)
        self.scale_bytes += sum(scales.numel() * scales.element_size() for _, scales in fields)
        return result

    def storage(self):
        tail = working = 0
        for layer in self.layers:
            count = layer.get_seq_length()
            if not count:
                continue
            working += (layer.keys.numel() + layer.values.numel()) * 4
            if self.tail_tokens and 'k' in self.mode:
                tail += layer.keys[..., -(1 + (count - 1) % 32):, :].numel() * 4
        return dict(payload_bytes=self.payload_bytes, scale_bytes=self.scale_bytes,
                    table_bytes=self.table_bytes, raw_tail_bytes=tail, untouched_fp32_bytes=self.untouched_bytes,
                    raw_bytes=self.payload_bytes + self.scale_bytes + self.table_bytes + tail + self.untouched_bytes,
                    baseline_fp32_bytes=self.incoming_bytes, reconstructed_cache_bytes=working)


@torch.inference_mode()
def evaluate_grouped(model, token_ids, codec, mode, k_layout='token', tail_tokens=32,
                     chunk_size=32, table_bytes=None):
    if (not isinstance(token_ids, torch.Tensor) or token_ids.dtype != torch.int64 or token_ids.ndim != 2
            or token_ids.shape[0] != 1 or token_ids.shape[1] < 2 or type(chunk_size) is not int
            or not 1 <= chunk_size <= 32):
        raise ValueError('evaluation needs one integer sequence and chunk size 1 through 32')
    cache = CausalGroupedCache(model.config, codec, mode, k_layout, tail_tokens, table_bytes)
    token_ids = token_ids.to(model.get_input_embeddings().weight.device)
    outputs = []
    position, length = 0, token_ids.shape[1] - 1
    while position < length:
        count = min(chunk_size, 32 - position % 32, length - position)
        outputs.append(model(input_ids=token_ids[:, position:position + count],
                             past_key_values=cache, use_cache=True).logits.float().cpu())
        position += count
    return torch.cat(outputs, dim=1), cache
