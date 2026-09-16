"""Bounded CPU scalar-cache diagnostic; see test-contract.md."""

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
from transformers import DynamicCache


def scalar_roundtrip(tensor, bits):
    if type(bits) is not int or bits not in (2, 4, 8):
        raise ValueError('bits must be 2, 4 or 8')
    if (not isinstance(tensor, torch.Tensor) or tensor.device.type != 'cpu'
            or tensor.dtype not in (torch.float32, torch.bfloat16) or tensor.ndim == 0
            or not tensor.numel() or tensor.shape[-1] % 32
            or not torch.isfinite(tensor).all()):
        raise ValueError('input must be a finite FP32/BF16 CPU tensor with groups of 32')
    groups = tensor.float().reshape(-1, 32)
    limit = (1 << (bits - 1)) - 1
    scales = groups.abs().amax(dim=1, keepdim=True) / limit
    scales = torch.where(scales == 0, torch.ones_like(scales), scales)
    if not torch.isfinite(scales).all():
        raise ValueError('input exceeds FP32 scale range')
    signed = (groups / scales).round().clamp(-limit, limit).to(torch.int32)
    shifts = torch.arange(8 // bits, dtype=torch.int32) * bits
    fields = (signed.flatten() & ((1 << bits) - 1)).reshape(-1, 8 // bits)
    packed = (fields << shifts).sum(dim=1).to(torch.uint8)
    decoded = (packed.to(torch.int32)[:, None] >> shifts) & ((1 << bits) - 1)
    decoded = torch.where(decoded >= 1 << (bits - 1), decoded - (1 << bits), decoded)
    restored = (decoded.reshape(-1, 32) * scales).reshape(tensor.shape).to(tensor.dtype)
    return restored, packed, scales


@torch.inference_mode()
def evaluate_prefix(model, token_ids, capture_path=None, capture_budget=128 * 1024 * 1024):
    if token_ids.ndim != 2 or token_ids.shape[0] != 1 or token_ids.shape[1] < 2:
        raise ValueError('token_ids must contain one sequence of at least two tokens')
    prefill = model(input_ids=token_ids[:, :-1], use_cache=True)
    original = [(layer.keys, layer.values) for layer in prefill.past_key_values.layers]
    snapshots = [(k.clone(), v.clone()) for k, v in original]
    elements = sum(x.numel() for pair in original for x in pair)

    def step(pairs):
        cache = DynamicCache(ddp_cache_data=[(k.clone(), v.clone()) for k, v in pairs])
        return model(input_ids=token_ids[:, -1:], past_key_values=cache,
                     use_cache=True).logits[0, -1].float()

    baseline = step(original)
    full = model(input_ids=token_ids, use_cache=False).logits[0, -1].float()
    if not torch.allclose(baseline, full, atol=1e-4, rtol=1e-4):
        raise AssertionError(f'cached/full baseline mismatch: max error {(baseline - full).abs().max().item()}')
    baseline_logprob = baseline.double().log_softmax(dim=-1)
    result = {'prefix_tokens': token_ids.shape[1] - 1,
              'baseline_fp32_bytes': elements * 4, 'baseline_bf16_bytes': elements * 2,
              'baseline_full_logit_max_error': (baseline - full).abs().max().item(),
              'variants': []}
    for bits in (2, 4, 8, 16):
        for mode in ('k', 'v', 'kv'):
            pairs = []
            payload_bytes = scale_bytes = untouched_bytes = 0
            errors, energies = {'k': 0.0, 'v': 0.0}, {'k': 0.0, 'v': 0.0}
            for pair in original:
                restored_pair = []
                for side, tensor in zip(('k', 'v'), pair):
                    if side not in mode:
                        restored = tensor
                        untouched_bytes += tensor.numel() * tensor.element_size()
                    elif bits == 16:
                        packed = tensor.to(torch.bfloat16)
                        restored = packed.float()
                        payload_bytes += packed.numel() * packed.element_size()
                    else:
                        restored, packed, scales = scalar_roundtrip(tensor, bits)
                        payload_bytes += packed.numel() * packed.element_size()
                        scale_bytes += scales.numel() * scales.element_size()
                    errors[side] += (tensor.double() - restored.double()).square().sum().item()
                    energies[side] += tensor.double().square().sum().item()
                    restored_pair.append(restored)
                pairs.append(tuple(restored_pair))
            logits = step(pairs)
            logprob = logits.double().log_softmax(dim=-1)
            metrics = {'bits': bits, 'mode': mode,
                       'format': 'bf16_roundtrip' if bits == 16 else 'symmetric_scalar_group32',
                       'payload_bytes': payload_bytes, 'scale_bytes': scale_bytes,
                       'untouched_fp32_bytes': untouched_bytes,
                       'stored_bytes': payload_bytes + scale_bytes + untouched_bytes,
                       'k_nmse': errors['k'] / energies['k'] if energies['k'] else 0.0,
                       'v_nmse': errors['v'] / energies['v'] if energies['v'] else 0.0,
                       'logit_rmse': (baseline.double() - logits.double()).square().mean().sqrt().item(),
                       'kl_original_to_variant': (baseline_logprob.exp() * (baseline_logprob - logprob)).sum().item(),
                       'top_token_agreement': baseline.argmax().item() == logits.argmax().item()}
            json.dumps(metrics, allow_nan=False)
            result['variants'].append(metrics)
    result['cache_unchanged'] = all(torch.equal(x, saved)
                                    for pair, saved_pair in zip(original, snapshots)
                                    for x, saved in zip(pair, saved_pair))
    if not result['cache_unchanged']:
        raise AssertionError('original prefix cache was mutated')
    if capture_path is not None:
        import numpy as np
        # Reserve room for NPZ headers as well as uncompressed tensor data.
        if elements * 4 + len(original) * 2048 < capture_budget:
            arrays = {f'layer_{i}_{side}': tensor.float().numpy()
                      for i, pair in enumerate(original)
                      for side, tensor in zip(('k', 'v'), pair)}
            np.savez(capture_path, **arrays)
            path = Path(capture_path)
            result['capture'] = {'file': path.name, 'bytes': path.stat().st_size,
                                 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                 'representation': 'original_fp32_prefix_kv'}
        else:
            result['capture'] = {'skipped': '128MiB cumulative capture budget'}
    json.dumps(result, allow_nan=False)
    return result


if __name__ == '__main__':
    import argparse
    import transformers

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path('/out'))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    torch.manual_seed(4827)
    revision = '1cfa9a7208912126459214e8b04321603b3df60c'
    snapshot = '/model/snapshots/' + revision
    metadata = {'scope': 'real_prefix_scalar_roundtrip_diagnostic',
                'model': 'Qwen/Qwen3-4B', 'model_revision': revision,
                'runtime': {'python': platform.python_version(), 'torch': torch.__version__,
                            'transformers': transformers.__version__, 'host': platform.node()},
                'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'dtype': 'float32', 'device': 'cpu', 'attention': 'eager',
                'seed': 4827, 'fitting': False, 'max_tokens': 64}
    print(json.dumps({'event': 'start', **metadata}, allow_nan=False), flush=True)
    tokenizer = transformers.AutoTokenizer.from_pretrained(
        snapshot, local_files_only=True, trust_remote_code=False)
    model = transformers.AutoModelForCausalLM.from_pretrained(
        snapshot, local_files_only=True, trust_remote_code=False,
        dtype=torch.float32, attn_implementation='eager').cpu().eval()
    prompts = [
        ('english_1', 'The library opens at nine and closes at six. Mira arrives at ten. Is the library open when she arrives?'),
        ('english_2', 'A red box contains three blue marbles and two green marbles. List the marble colors and their counts.'),
        ('portuguese_1', 'A biblioteca abre às nove e fecha às dezoito. Ana chega às dez. A biblioteca está aberta quando ela chega?'),
        ('portuguese_2', 'Uma caixa contém três bolas azuis e duas verdes. Liste as cores das bolas e a quantidade de cada cor.'),
        ('code_1', 'def total_even(values):\n    return sum(x for x in values if x % 2 == 0)\n\n# Evaluate total_even([1, 2, 3, 4]):'),
        ('code_2', 'SELECT category, COUNT(*) FROM items GROUP BY category;\n-- Explain what one output row represents:'),
    ]
    remaining_capture_bytes = 128 * 1024 * 1024
    for prompt_id, prompt in prompts:
        token_ids = tokenizer(prompt, return_tensors='pt', truncation=True, max_length=64)['input_ids']
        result = evaluate_prefix(model, token_ids, args.output_dir / f'{prompt_id}-prefix.npz',
                                 remaining_capture_bytes)
        remaining_capture_bytes -= result.get('capture', {}).get('bytes', 0)
        print(json.dumps({'event': 'prompt', **metadata, 'prompt_id': prompt_id,
                          'prompt': prompt, 'token_ids': token_ids[0].tolist(), **result},
                         allow_nan=False), flush=True)
    print(json.dumps({'event': 'complete', 'prompt_cases': len(prompts),
                      'variant_cases': len(prompts) * 12,
                      'capture_bytes': 128 * 1024 * 1024 - remaining_capture_bytes}), flush=True)
