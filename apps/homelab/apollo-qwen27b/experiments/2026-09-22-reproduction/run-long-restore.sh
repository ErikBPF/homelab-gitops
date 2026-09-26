#!/usr/bin/env bash
# Start the LMCache + vLLM trial, then prove a long-prefix external restore.
#
# MAX_MODEL_LEN / GPU_UTIL / L1_GB / RETRIEVAL_TOKENS / TAG select the phase.
# Containers are left running on success so paired benchmark receipts can be
# collected; the caller stops them (podman stop/rm) between phases.
set -euo pipefail

trial=/mnt/data/ai/validation/20260922-qwen38-repro
results=$trial/results
tag=${TAG:?set TAG}
retrieval_tokens=${RETRIEVAL_TOKENS:?set RETRIEVAL_TOKENS}
engine_name=${ENGINE_NAME:-apollo-qwen38-gittensor-lmcache}
podman=(sudo -n /mnt/microvms/ai-evaluation/runtimes/tools/podman/bin/podman
    --root /mnt/microvms/ai/cache/podman-root --runroot /run/apollo-ftw-containers)

mkdir -p "$results"
"$trial/run-lmcache.sh"
"${podman[@]}" logs "$engine_name" > "$results/gittensor-lmcache-$tag-startup.log" 2>&1 || true
"${podman[@]}" logs "${SERVER_NAME:-apollo-qwen38-lmcache-server}" \
    > "$results/lmcache-server-$tag.log" 2>&1 || true

set +e
"${podman[@]}" exec "$engine_name" python3 /trial/probe_restore.py \
    --target-tokens "$retrieval_tokens" --tag "$tag" \
    > "$results/gittensor-lmcache-$tag-probe.json" \
    2> "$results/gittensor-lmcache-$tag-probe.err"
rc=$?
set -e
cat "$results/gittensor-lmcache-$tag-probe.json" || true

if (( rc != 0 )); then
    "${podman[@]}" logs "$engine_name" > "$results/gittensor-lmcache-$tag-fail.log" 2>&1 || true
    echo "LONG RESTORE FAILED ($tag); see $results/gittensor-lmcache-$tag-probe.err" >&2
    exit "$rc"
fi
echo "LONG RESTORE OK ($tag)"
