#!/usr/bin/env bash
# Apollo vLLM server for Qwen3.8-27B NVFP4 (TP=2), LMCache connector enabled.
# Regenerated 2026-10-11 from the running container `apollo-qwen38-gittensor-lmcache`
# (vLLM v0.30.0). Keep this and launch.sh in sync with the live stack.
set -euo pipefail

export LMCACHE_TRACK_USAGE=false
export VLLM_SERVER_DEV_MODE=1
export VLLM_CACHE_ROOT=/cache/vllm
export HF_HOME=/cache/hf
export XDG_CACHE_HOME=/cache/xdg

exec vllm serve /model \
  --served-model-name qwen38-27b-nvfp4 \
  --host 100.77.14.27 --port 11542 \
  --tensor-parallel-size 2 \
  --no-enable-flashinfer-autotune --disable_custom_all_reduce \
  --load-format ipc_cache --trust-remote-code \
  --kv-cache-dtype fp8 \
  --max-model-len 200000 --max-num-seqs 8 \
  --compilation-config '{"cudagraph_mode":"PIECEWISE"}' \
  --mamba-cache-dtype bfloat16 --mamba-ssm-cache-dtype bfloat16 \
  --mamba-cache-mode align \
  --gpu_memory_utilization 0.90 \
  --max-num-batched-tokens 1727 \
  --enable-prefix-caching --enable-chunked-prefill \
  --limit-mm-per-prompt '{"video":0}' \
  --mm-processor-kwargs '{"max_pixels":1000000}' \
  --reasoning-parser qwen3 --tool-call-parser qwen3_xml --enable-auto-tool-choice \
  --default-chat-template-kwargs '{"preserve_thinking":true}' \
  --speculative-config '{"method":"mtp","num_speculative_tokens":6}' \
  --kv-transfer-config '{"kv_connector":"LMCacheMPConnector","kv_connector_module_path":"lmcache.integration.vllm.lmcache_mp_connector","kv_role":"kv_both","kv_connector_extra_config":{"lmcache.mp.host":"127.0.0.1","lmcache.mp.port":11550,"lmcache.mp.autostart":false}}'
