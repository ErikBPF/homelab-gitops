"""Two-device native-forward regression for the Quanto execution context."""
import torch
from accelerate import dispatch_model
from transformers import Qwen3Config, Qwen3ForCausalLM
import stock_q4_probe as probe
from stock_q4_probe import evaluate, ARMS

torch.set_num_threads(2)
torch.manual_seed(4827)
assert torch.cuda.device_count() == 2
config=Qwen3Config(vocab_size=64,hidden_size=128,intermediate_size=128,
                  num_hidden_layers=2,num_attention_heads=2,num_key_value_heads=2,head_dim=64)
config._attn_implementation='eager'
model=Qwen3ForCausalLM(config).float().eval()
model=dispatch_model(model,device_map={'model.embed_tokens':0,'lm_head':0,
    'model.layers.0':0,'model.layers.1':1,'model.norm':1,'model.rotary_emb':1})
entry=torch.cuda.current_device()
original=[len(layer._forward_pre_hooks) for layer in model.model.layers]
# Assert context before the extension can launch on the wrong device.
def check_device(module, inputs):
    assert torch.cuda.current_device()==next(module.parameters()).device.index
ids=(torch.arange(130)%64).reshape(1,-1)
handles=[layer.register_forward_pre_hook(check_device) for layer in model.model.layers]
try:
    clean,_=evaluate(model,ids,'clean')
    for arm in ARMS:
        actual,storage=evaluate(model,ids,arm)
        assert actual.shape==clean.shape and torch.isfinite(actual).all()
        print(arm,'two-GPU PASS',flush=True)
finally:
    for handle in handles:handle.remove()
assert torch.cuda.current_device()==entry
assert [len(layer._forward_pre_hooks) for layer in model.model.layers]==original
try:
    with probe.decoder_devices(model):
        torch.cuda.set_device(1-entry)
        raise RuntimeError('check cleanup')
except RuntimeError:
    pass
assert torch.cuda.current_device()==entry
assert [len(layer._forward_pre_hooks) for layer in model.model.layers]==original
print('two-GPU context/cleanup PASS',flush=True)
