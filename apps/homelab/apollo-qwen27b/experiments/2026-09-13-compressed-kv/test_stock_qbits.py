"""Precision-width contract; run in the qualified Torch/Quanto runtime."""
import sys
import torch
from transformers import Qwen3Config, Qwen3ForCausalLM, GraniteConfig, GraniteForCausalLM
from transformers.cache_utils import QuantizedCache
import stock_q4_probe as probe

assert hasattr(probe, 'linear_roundtrip'), 'Q2-Q6 roundtrip is missing'
torch.set_num_threads(2)
torch.manual_seed(4827)
for device in ['cpu'] + (['cuda:0'] if torch.cuda.is_available() else []):
    x = torch.cat((torch.linspace(-1, 1, 128), torch.zeros(32))).to(device).reshape(5, 32)
    for bits in range(2, 7):
        y, packed, scales = probe.linear_roundtrip(x, bits)
        limit = 2 ** (bits - 1) - 1
        expected_scales = torch.where(x.abs().amax(-1, keepdim=True) == 0,
                                      torch.ones_like(scales), x.abs().amax(-1, keepdim=True) / limit)
        expected = (x / expected_scales).round().clamp(-limit, limit)
        assert torch.equal(scales, expected_scales)
        assert torch.equal(y, expected * scales)
        assert packed.dtype == torch.uint8 and packed.numel() == x.numel() * bits // 8
        if bits == 4:
            old = probe.grid_roundtrip(x, torch.arange(1, 8, dtype=torch.float32) / 7, legacy_linear=True)
            assert all(torch.equal(a, b) for a, b in zip((y, packed, scales), old))
        else:
            # Independent Python bit-plane decoder checks the actual packed bytes.
            decoded = []
            values = packed.cpu().tolist()
            for offset in range(0, len(values), bits):
                for index in range(8):
                    value = sum(((values[offset + bit] >> index) & 1) << bit for bit in range(bits))
                    decoded.append(value - (1 << bits) if value >= (1 << (bits - 1)) else value)
            assert decoded == expected.flatten().int().cpu().tolist()
for invalid in (1, 7, True, 2.0):
    try:
        probe.linear_roundtrip(torch.zeros(32), invalid)
    except ValueError:
        pass
    else:
        raise AssertionError('invalid width accepted')
for invalid in (torch.ones(31), torch.full((32,), float('nan')), torch.ones(32).half()):
    try:
        probe.linear_roundtrip(invalid, 3)
    except ValueError:
        pass
    else:
        raise AssertionError('invalid input accepted')
config = Qwen3Config(vocab_size=64, hidden_size=128, intermediate_size=128,
                    num_hidden_layers=1, num_attention_heads=2, num_key_value_heads=2, head_dim=64)
for bits in range(2, 7):
    for arm in probe.ARMS[:2]:
        if bits in (2, 4):
            assert probe.make_cache(config, arm, bits).layers[0].nbits == bits
        else:
            try:
                probe.make_cache(config, arm, bits)
            except ValueError:
                pass
            else:
                raise AssertionError('unsupported stock width accepted')
    if bits not in (2, 4):
        try:
            QuantizedCache(backend='quanto', config=config, nbits=bits)
        except ValueError as error:
            print('upstream unsupported', bits, str(error), flush=True)
        else:
            raise AssertionError('upstream support assumption changed')
for cls, cfg in [(Qwen3ForCausalLM, config), (GraniteForCausalLM, GraniteConfig(
        vocab_size=64, hidden_size=128, intermediate_size=128, num_hidden_layers=1,
        num_attention_heads=2, num_key_value_heads=2, max_position_embeddings=512))]:
    cfg._attn_implementation = 'eager'
    model = cls(cfg).float().to('cuda:0' if torch.cuda.is_available() else 'cpu').eval()
    ids = (torch.arange(258) % 64).reshape(1, -1)
    clean, _ = probe.evaluate(model, ids, 'clean')
    for bits in range(2, 7):
        for arm in probe.ARMS if bits in (2, 4) else probe.ARMS[2:]:
            actual, storage = probe.evaluate(model, ids, arm, bits)
            assert actual.shape == clean.shape and torch.isfinite(actual).all()
            if arm.startswith('own-'):
                ledger = storage['encoded_ledger']
                assert ledger['payload_bytes'] * 32 == (ledger['baseline_fp32_bytes'] - ledger['raw_tail_bytes']) * bits
            print(cls.__name__, bits, arm, 'PASS', flush=True)
print('Q2-Q6 contract PASS', flush=True)
