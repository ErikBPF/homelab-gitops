#!/usr/bin/env bash
set -euo pipefail
export LMCACHE_TRACK_USAGE=false PYTHONHASHSEED=0 LMCACHE_LOG_LEVEL=INFO
exec python3 -m lmcache.v1.multiprocess.server --host 127.0.0.1 --port 11550 --chunk-size 1600 --l1-size-gb 8 --eviction-policy LRU --max-gpu-workers 2 --max-cpu-workers 2 --disable-metrics --enable-extra-logging
