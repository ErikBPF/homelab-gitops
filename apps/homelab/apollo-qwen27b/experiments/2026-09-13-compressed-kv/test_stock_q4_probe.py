"""Native cache comparison contract; run with python test_stock_q4_probe.py."""
from pathlib import Path
from types import SimpleNamespace
assert Path(__file__).with_name('stock_q4_probe.py').exists(), 'stock Q4 comparison runner is missing'
import sys
import torch
from transformers import Qwen3Config, Qwen3ForCausalLM, GraniteConfig, GraniteForCausalLM
from transformers.cache_utils import QuantizedCache, QuantoQuantizedLayer
from stock_q4_probe import make_cache, tensor_bytes, evaluate, ARMS, model_placement

torch.set_num_threads(2)
torch.manual_seed(4827)
single_gpu = SimpleNamespace(parameters=lambda: iter([
    SimpleNamespace(dtype=torch.float32, device=torch.device('cuda:0'))]))
try:
    placement = model_placement(single_gpu)
except AttributeError as error:
    raise AssertionError('single-GPU placement must not require hf_device_map') from error
assert placement == dict(parameter_devices=['cuda:0'], device_map=None)
for dtype, device in [(torch.float32, 'cpu'), (torch.float16, 'cuda:0')]:
    invalid = SimpleNamespace(parameters=lambda: iter([
        SimpleNamespace(dtype=dtype, device=torch.device(device))]))
    try:
        model_placement(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError('invalid parameter placement accepted')
config = Qwen3Config(vocab_size=64, hidden_size=128, intermediate_size=128,
                     num_hidden_layers=1, num_attention_heads=2, num_key_value_heads=2, head_dim=64)
cache = make_cache(config, 'stock-default')
assert type(cache) is QuantizedCache and type(cache.layers[0]) is QuantoQuantizedLayer
layer = cache.layers[0]
assert (layer.nbits, layer.axis_key, layer.axis_value, layer.q_group_size, layer.residual_length) == (4, 0, 0, 64, 128)
x = torch.randn(1, 8, 1, 64)
cache.update(x, x.clone(), 0)
q = layer._quantized_keys
assert tensor_bytes(q) == 256 + 32 + 32, tensor_bytes(q)
assert tensor_bytes([q, q]) == 320
raw = torch.zeros(100)
assert tensor_bytes([raw, raw[10:20]]) == 400
assert tensor_bytes([layer._quantized_keys, layer._quantized_values, layer.keys, layer.values]) == 640
try:
    make_cache(config, 'invalid')
except ValueError:
    pass
else:
    raise AssertionError('unknown cache variant accepted')
for cls, cfg in [(Qwen3ForCausalLM, config), (GraniteForCausalLM, GraniteConfig(
        vocab_size=64, hidden_size=128, intermediate_size=128, num_hidden_layers=1,
        num_attention_heads=2, num_key_value_heads=2, max_position_embeddings=512))]:
    cfg._attn_implementation = 'eager'
    model = cls(cfg).float().to(sys.argv[1] if len(sys.argv) > 1 else 'cpu').eval()
    ids = (torch.arange(258) % 64).reshape(1, -1)
    clean, clean_storage = evaluate(model, ids, 'clean')
    repeat, _ = evaluate(model, ids, 'clean')
    assert torch.equal(clean, repeat)
    for arm in ARMS:
        actual, storage = evaluate(model, ids, arm)
        assert actual.shape == clean.shape and torch.isfinite(actual).all()
        assert storage['end_resident_bytes'] > 0
        assert storage['maximum_sampled_resident_bytes'] >= storage['end_resident_bytes']
        if arm.startswith('own-'):
            assert storage['end_resident_bytes'] == clean_storage['end_resident_bytes']
            assert storage['encoded_ledger']['raw_bytes'] < storage['end_resident_bytes']
        else:
            assert storage['end_resident_bytes'] < clean_storage['end_resident_bytes']
    print(cls.__name__, 'PASS', flush=True)
print('stock Q4 contract PASS', flush=True)
