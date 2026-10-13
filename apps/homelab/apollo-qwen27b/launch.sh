#!/usr/bin/env bash
# Apollo Qwen3.8-27B NVFP4 runtime: one host Podman container running LMCache and
# vLLM (see supervise.sh). Verified on Apollo 2026-10-12.
#
# `start` runs it detached (manual use); `run` runs it in the foreground for a
# systemd unit. The deployed unit lives in desktop-nixos; this script is the
# host-side reference/manual path.
set -euo pipefail

RUNTIME=${RUNTIME:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}
ROOT=${PODMAN_ROOT:-/mnt/data/ai/cache/podman-root}
RUNROOT=${PODMAN_RUNROOT:-/run/apollo-ftw-containers}
MODEL_DIR=${MODEL_DIR:-/mnt/data/ai/models/gittensor/Qwen3.8-27B-NVFP4-RTX5090-0cc2795}
CACHE_DIR=${CACHE_DIR:-/mnt/data/ai/cache}
NAME=${NAME:-apollo-qwen38}
# Local patched build (experiments/2026-09-22-reproduction/Containerfile.lmcache).
# A local build has no repository digest, so pin the image id; rebuild and re-pin
# when that Containerfile changes.
IMAGE=${IMAGE:-sha256:dce379422d6420af4006eacb78c677d2e864446858ab6b31b28aad2e86c6a7bf}

if [[ ${EUID} -eq 0 ]]; then SUDO=(); else SUDO=(sudo -n); fi
if [[ -z ${PODMAN_BIN:-} ]]; then
  candidate=/mnt/data/ai-evaluation/runtimes/tools/podman/bin/podman
  [[ -x $candidate ]] && PODMAN_BIN=$candidate
fi
PODMAN_BIN=${PODMAN_BIN:-$(command -v podman)}
podman=("${SUDO[@]}" "$PODMAN_BIN" --root "$ROOT" --runroot "$RUNROOT")

preflight() {
  # The backend has no API credential: refuse a lost or bypassed firewall.
  # `apollo-qwen-litellm` covers 11542 (vLLM) and 11560 (pqnld); note the
  # tailscale `ts-input` chain precedes these jumps and accepts tailscale0, so
  # the allowlist constrains non-tailscale sources only.
  local input_rules chain_rules gpu_uuids
  input_rules=$("${SUDO[@]}" iptables -S INPUT)
  chain_rules=$("${SUDO[@]}" iptables -S apollo-qwen-litellm)
  grep -qxF -- '-A INPUT -d 100.77.14.27/32 -p tcp -m tcp --dport 11542 -j apollo-qwen-litellm' <<<"$input_rules"
  grep -qxF -- '-A INPUT -d 100.77.14.27/32 -p tcp -m tcp --dport 11560 -j apollo-qwen-litellm' <<<"$input_rules"
  [[ $chain_rules == '-N apollo-qwen-litellm
-A apollo-qwen-litellm -i lo -j ACCEPT
-A apollo-qwen-litellm -s 100.103.52.100/32 -i tailscale0 -p tcp -m multiport --dports 11542,11560 -j ACCEPT
-A apollo-qwen-litellm -j REJECT --reject-with icmp-port-unreachable' ]]
  [[ $(findmnt -n -o SOURCE /mnt/data) == /dev/sdc1 ]]
  # Both cards must be present; index order varies across boots, so match UUIDs.
  gpu_uuids=$(nvidia-smi --query-gpu=uuid --format=csv,noheader)
  [[ $gpu_uuids == *GPU-af808452-3544-e84a-b33a-fc5588ede436* ]]
  [[ $gpu_uuids == *GPU-818843e6-ce30-5d98-a4b4-8d8580aaa049* ]]
  "${podman[@]}" image exists "$IMAGE"
  [[ -r $RUNTIME/supervise.sh && -r $RUNTIME/serve-vllm.sh && -r $RUNTIME/serve-lmcache.sh ]]
  [[ -r $MODEL_DIR/config.json ]]
}

stop() {
  "${podman[@]}" stop -t 60 "$NAME" >/dev/null 2>&1 || true
  "${podman[@]}" rm -f "$NAME" >/dev/null 2>&1 || true
}

run_args=(
  --name "$NAME" --pull=never --network=host --shm-size=8g
  --user 1000:100 --cpus=8 --memory=80g --memory-swap=80g
  --security-opt=no-new-privileges --device nvidia.com/gpu=all
  -v "$MODEL_DIR:/model:ro" -v "$CACHE_DIR:/cache" -v "$RUNTIME:/trial:ro"
  --entrypoint /bin/bash "$IMAGE" /trial/supervise.sh
)

start() {
  preflight
  stop
  "${podman[@]}" run -d "${run_args[@]}"
}

# Foreground supervisor mode for a systemd unit; exec so signals reach podman.
run() {
  preflight
  stop
  exec "${podman[@]}" run --rm "${run_args[@]}"
}

case ${1:-start} in
  preflight) preflight ;;
  start) start ;;
  run) run ;;
  down) stop ;;
  status)
    "${podman[@]}" ps -a --filter name="$NAME" --format '{{.Names}} {{.Image}} {{.Status}}'
    ;;
  *)
    echo "usage: $0 {start|run|down|status|preflight}" >&2
    exit 2
    ;;
esac
