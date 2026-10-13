#!/usr/bin/env bash
# Apollo LMCache server. Runs in the same container as vLLM (see supervise.sh):
# KV-cache registration only completes when both share the CUDA/IPC namespace.
#
# L1 is host RAM (8 GiB); the two GPU workers exist so vLLM's KV registration
# succeeds. Verified on Apollo 2026-10-12.
set -euo pipefail

export LMCACHE_TRACK_USAGE=false
export PYTHONHASHSEED=0

exec lmcache server \
  --host 127.0.0.1 --port 11550 \
  --http-host 127.0.0.1 --http-port 11552 --prometheus-port 11551 \
  --chunk-size 864 --separate-object-groups \
  --l1-size-gb 8 --l1-init-size-gb 8 \
  --eviction-policy LRU \
  --max-gpu-workers 2 --max-cpu-workers 2
