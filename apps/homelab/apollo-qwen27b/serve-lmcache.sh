#!/usr/bin/env bash
# Apollo LMCache server (multiprocess, CPU/host RAM L1 only).
# Regenerated 2026-10-11 from the running container `apollo-qwen38-lmcache-server`.
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
