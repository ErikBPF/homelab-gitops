#!/usr/bin/env bash
# Run LMCache and vLLM as two processes of one container. Either exiting makes
# the combined service unavailable, so the container (and the unit that owns it)
# restarts. Logs go to the container's stdout/stderr.
set -euo pipefail

children=()
cleanup() {
  trap - EXIT TERM INT
  kill -TERM "${children[@]}" 2>/dev/null || true
  wait || true
}
trap cleanup EXIT TERM INT

bash /trial/serve-lmcache.sh &
children+=("$!")
# Give LMCache a head start so vLLM's registration finds the MP server up.
sleep 8
bash /trial/serve-vllm.sh &
children+=("$!")

wait -n "${children[@]}"
exit 1
