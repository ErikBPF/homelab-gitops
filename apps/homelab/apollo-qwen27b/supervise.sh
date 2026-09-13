#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH=/trial/lmcache-site:/site
children=()
cleanup() {
  trap - EXIT TERM INT
  kill -TERM "${children[@]}" 2>/dev/null || true
  wait || true
}
trap cleanup EXIT TERM INT
bash /trial/serve-lmcache.sh >> /trial/lmcache.log 2>&1 &
children+=("$!")
bash /trial/serve-vllm.sh >> /trial/vllm.log 2>&1 &
children+=("$!")
wait -n "${children[@]}"
# A clean child exit still means the combined service is unavailable.
exit 1
