#!/usr/bin/env bash
# Apollo vLLM server for Qwen3.8-27B NVFP4 (TP=2) with the LMCache connector.
#
# Verified on Apollo 2026-10-12 (the configuration the host actually runs):
#   max_model_len 105000 (probed ceiling with LMCache; 107500 fails),
#   max-num-seqs 4, gpu-memory-utilization 0.80.
# 0.80 (not 0.90) is required: LMCache MV registration needs GPU headroom and
# OOMs at 0.90. LMCache must run in this same container (see supervise.sh) --
# cross-container KV registration never completed.
set -euo pipefail

export VLLM_SERVER_DEV_MODE=1
export VLLM_CACHE_ROOT=/cache/vllm
export HF_HOME=/cache/hf
export XDG_CACHE_HOME=/cache/xdg
export TORCH_HOME=/cache/torch
export CUDA_CACHE_PATH=/cache/cuda
export HF_HUB_OFFLINE=1
export NCCL_P2P_DISABLE=1
export VLLM_ALLOW_LONG_MAX_MODEL_LEN=1
export LMCACHE_TRACK_USAGE=false

# --host 0.0.0.0: pqnld reaches vLLM over loopback, so binding only the tailnet
# IP is not enough. Reachability is constrained by the apollo-qwen-litellm chain.
exec vllm serve /model \
  --served-model-name qwen38-27b-nvfp4 \
  --host 0.0.0.0 --port 11542 \
  --tensor-parallel-size 2 \
  --no-enable-flashinfer-autotune --disable_custom_all_reduce --trust-remote-code \
  --kv-cache-dtype fp8 \
  --max-model-len 105000 --max-num-seqs 4 \
  --compilation-config '{"cudagraph_mode":"PIECEWISE"}' \
  --mamba-cache-dtype bfloat16 --mamba-ssm-cache-dtype bfloat16 --mamba-cache-mode align \
  --gpu-memory-utilization 0.80 \
  --max-num-batched-tokens 1727 \
  --enable-prefix-caching --enable-chunked-prefill \
  --limit-mm-per-prompt '{"video":0}' \
  --mm-processor-kwargs '{"max_pixels":1000000}' \
  --reasoning-parser qwen3 --tool-call-parser qwen3_xml --enable-auto-tool-choice \
  --default-chat-template-kwargs '{"preserve_thinking":true}' \
  --speculative-config '{"method":"mtp","num_speculative_tokens":6}' \
  --kv-transfer-config '{"kv_connector":"LMCacheMPConnector","kv_connector_module_path":"lmcache.integration.vllm.lmcache_mp_connector","kv_role":"kv_both","kv_connector_extra_config":{"lmcache.mp.host":"127.0.0.1","lmcache.mp.port":11550,"lmcache.mp.autostart":false}}'
