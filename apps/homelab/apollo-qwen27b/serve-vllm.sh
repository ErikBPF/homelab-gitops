#!/usr/bin/env bash
set -euo pipefail
export MAX_JOBS=2 NVCC_THREADS=1 CMAKE_BUILD_PARALLEL_LEVEL=2 TORCHINDUCTOR_COMPILE_THREADS=1
export FLASHINFER_WORKSPACE_BASE=/cache/user1000 TRITON_CACHE_DIR=/cache/user1000/triton
export LMCACHE_TRACK_USAGE=false PYTHONHASHSEED=0 VLLM_SERVER_DEV_MODE=1
export PYTHONPATH=/trial/lmcache-site:/site
exec python3 -m vllm.entrypoints.cli.main serve /model \
 --host 100.77.14.27 --port 11542 --served-model-name qwen38-27b-nvfp4 \
 --tensor-parallel-size 2 --distributed-executor-backend mp --disable-custom-all-reduce \
 --max-model-len 90000 --kv-cache-dtype fp8_e4m3 --max-num-seqs 1 --max-num-batched-tokens 1600 \
 --gpu-memory-utilization 0.85 --no-enable-flashinfer-autotune --language-model-only \
 --speculative-config '{"method":"mtp","num_speculative_tokens":2}' \
 --enable-auto-tool-choice --tool-call-parser qwen3_coder \
 --kv-transfer-config '{"kv_connector":"LMCacheMPConnector","kv_connector_module_path":"lmcache.integration.vllm.lmcache_mp_connector","kv_role":"kv_both","kv_connector_extra_config":{"lmcache.mp.host":"127.0.0.1","lmcache.mp.port":11550}}'
