"""Per-card CUDA numerical qualification; no performance or model-quality claim."""

import hashlib
import json
import math
from pathlib import Path

import torch
import torch.nn.functional as functional
from torch.nn.attention import SDPBackend, sdpa_kernel


def main():
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    torch.manual_seed(4827)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False

    def emit(event, **record):
        print(json.dumps(dict(event=event, **record), allow_nan=False), flush=True)

    visible = torch.cuda.device_count()
    properties = torch.cuda.get_device_properties(0) if visible else None
    emit('provenance', seed=4827, torch_version=torch.__version__, cuda_version=torch.version.cuda,
         compiled_architectures=torch.cuda.get_arch_list(), visible_gpu_count=visible,
         device=torch.cuda.get_device_name(0) if visible else None,
         capability=list(torch.cuda.get_device_capability(0)) if visible else None,
         device_uuid=str(getattr(properties, 'uuid', 'unavailable')),
         tf32=False, threads=4, scope='numerical_runtime_qualification_only',
         source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         torch_module_sha256=hashlib.sha256(Path(torch.__file__).read_bytes()).hexdigest(),
         functional_module_sha256=hashlib.sha256(Path(functional.__file__).read_bytes()).hexdigest())
    if visible != 1:
        raise AssertionError(f'exactly one visible GPU required; observed {visible}')

    cases = [('matmul', 'float32', None, None), ('matmul', 'bfloat16', None, None)]
    cases += [('sdpa', 'bfloat16', backend, phase)
              for backend in ('MATH', 'FLASH_ATTENTION') for phase in ('prefill', 'decode')]
    failures = 0
    for operation, dtype_name, backend, phase in cases:
        if operation == 'matmul':
            atol, rtol = (1e-4, 1e-4) if dtype_name == 'float32' else (0.1, 0.01)
            relative_limit = 1e-5 if dtype_name == 'float32' else 0.01
        else:
            atol, rtol, relative_limit = 0.02, 0.0, 0.01
        metadata = dict(operation=operation, dtype=dtype_name, backend=backend, phase=phase,
                        atol=atol, rtol=rtol, max_absolute_limit=atol, relative_l2_limit=relative_limit)
        try:
            dtype = getattr(torch, dtype_name)
            generator = torch.Generator().manual_seed(4827)
            if operation == 'matmul':
                left = torch.randn(31, 47, generator=generator).to(dtype)
                right = torch.randn(47, 29, generator=generator).to(dtype)
                reference = left.double() @ right.double()
                metadata['shapes'] = [list(left.shape), list(right.shape)]
                actual = left.cuda() @ right.cuda()
            else:
                query_length, key_length, causal = (33, 33, True) if phase == 'prefill' else (1, 129, False)
                query = torch.randn(1, 4, query_length, 128, generator=generator).to(dtype)
                key = torch.randn(1, 4, key_length, 128, generator=generator).to(dtype)
                value = torch.randn(1, 4, key_length, 128, generator=generator).to(dtype)
                scores = query.double() @ key.double().transpose(-2, -1) / math.sqrt(128)
                if causal:
                    mask = torch.ones(query_length, key_length, dtype=torch.bool).triu(1)
                    scores.masked_fill_(mask, float('-inf'))
                reference = scores.softmax(dim=-1) @ value.double()
                metadata.update(shapes=[list(t.shape) for t in (query, key, value)], causal=causal)
                with sdpa_kernel(backends=[getattr(SDPBackend, backend)]):
                    actual = functional.scaled_dot_product_attention(
                        query.cuda(), key.cuda(), value.cuda(), dropout_p=0.0, is_causal=causal)
            torch.cuda.synchronize()
            actual = actual.cpu().double()
            if not torch.isfinite(reference).all() or not torch.isfinite(actual).all():
                raise AssertionError('nonfinite reference or CUDA result')
            error = actual - reference
            metadata.update(max_absolute_error=error.abs().max().item(),
                            relative_l2_error=(error.norm() / reference.norm()).item())
            if metadata['max_absolute_error'] > atol:
                raise AssertionError('maximum absolute error exceeds declared tolerance')
            torch.testing.assert_close(actual, reference, atol=atol, rtol=rtol)
            if metadata['relative_l2_error'] > relative_limit:
                raise AssertionError('relative L2 error exceeds declared tolerance')
            emit('cell', status='pass', **metadata)
        except Exception as error:
            failures += 1
            emit('cell', status='fail', error_type=type(error).__name__, error=str(error), **metadata)
    emit('complete', cells=len(cases), passed=len(cases) - failures, failed=failures)
    return int(failures > 0)


if __name__ == '__main__':
    raise SystemExit(main())
