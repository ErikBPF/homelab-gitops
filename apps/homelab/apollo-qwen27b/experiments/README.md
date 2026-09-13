# Apollo Unsloth DFlash2 experiment

Validated 2026-09-11 on two RTX 5060 Ti 16 GB GPUs, with no CPU offload.
The personal service launch files are unchanged. This is a localhost-only,
time-bounded host-container trial, not a deployed service change.

## Revisions and fix

- vLLM `3116c5d06bfe76501b3dd6b5434bfc7f3274f5e7`, V2 model runner.
- `unsloth/Qwen3.8-27B-NVFP4` at
  `f0b7c9e722f5565102fff8481c99e4d86ae099c7`.
- `z-lab/Qwen3.8-27B-DFlash2` at
  `50307d4c4cde6860d4eee73e2547cd786fe8e8a4`.

The unpatched loader allocates full-vocabulary BF16 embedding/head placeholders
before sharing the target's tensors. That transient causes GPU OOM on this
combination. See [upstream issue 53612](https://github.com/vllm-project/vllm/issues/53612).

Apply `defer-shared-vocab.patch` with `patch -p1` inside an isolated copy of the
pinned vLLM package root. Set `"defer_shared_vocab": true` in a separate copy of
the drafter config. It creates those two placeholders on PyTorch's meta device;
the existing loader replaces them with target tensors already on GPU. No
vocabulary truncation or CPU weight offload. The opt-in loader rejects private
vocabulary weights, and the V2 sharing path rejects leftover meta parameters.
The verified drafter has no private embedding/head/mapping tensors. Other
drafter checkpoints are not qualified by this experiment.

## Working recipe and check

TP=2, MTP disabled, DFlash seven speculative tokens, context 16384,
`--kv-cache-dtype int8_per_token_head --attention-backend TRITON_ATTN`,
`--gpu-memory-utilization 0.93 --max-num-seqs 1 --max-num-batched-tokens 512`.
No `--cpu-offload-gb`, fixed KV allocation, or LMCache connector.
The 3070 is excluded. INT8 per-token/head KV is distinct from llama.cpp Q8_0.

Remote trial:
`/mnt/microvms/ai/validation/20260911-unsloth-dflash2-shared-vocab`.
Its `site/vllm` is a private package copy, `draft/config.json` is a separate
config, and `serve-dflash-int8-16k.sh` is the validated launch command.
The container uses `PYTHONPATH=/trial/site:/site` for this overlay.

The existing six-request `bench.py` remains the runnable regression check:
inside the trial container, run
`python3 /trial/bench.py vllm /trial/recheck.json` after health succeeds.
It asserts six completed streams and 256 generated tokens per request.
`bench-context.py` also passed with 14012 prompt tokens and 256 output tokens.
The scripts, requests, responses, metrics, logs, and patch are embedded in the
coordination repo's `docs/audits/2026-09-11-apollo-unsloth-dflash2.json`.

All six short requests passed: median decode 57.24 tok/s prose and
130.81 tok/s code. The 14K prompt took 10.665 s to first token and decoded at
40.05 tok/s. These are synthetic throughput checks, not model-quality scores.

Rollback: stop `apollo-unsloth-dflash2-shared`; use the untouched original
package, drafter config, and previous launch recipe. Do not promote the overlay
or change the personal route without a separate deployment change. The trial
container has a 5400-second timeout and no restart policy.
