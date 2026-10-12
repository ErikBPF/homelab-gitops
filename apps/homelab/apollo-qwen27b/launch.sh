#!/usr/bin/env bash
# Apollo Qwen3.8-27B NVFP4 runtime: vLLM (TP=2) + LMCache as two host Podman
# containers. Regenerated 2026-10-11 from the running stack; see README.md.
#
# Host-side only: it creates and stops the containers, it does not build images.
# The systemd unit `apollo-ai.service` runs `launch.sh run` (foreground) with
# Restart=always; `install.sh` places this file in /etc/apollo-ai.
set -euo pipefail

ROOT=${PODMAN_ROOT:-/mnt/data/ai/cache/podman-root}
RUNROOT=${PODMAN_RUNROOT:-/run/apollo-ftw-containers}
RUNTIME=${RUNTIME:-/etc/apollo-ai}
MODEL_DIR=${MODEL_DIR:-/mnt/data/ai/models/gittensor/Qwen3.8-27B-NVFP4-RTX5090-0cc2795}
CACHE_DIR=${CACHE_DIR:-/mnt/data/ai/cache}
# Pinned by digest. VLLM_IMAGE is the locally patched build of Containerfile.lmcache
# (LMCache PR 4731 + Apollo packed-KV layout); rebuild and re-pin its id when that
# Containerfile changes. LMCACHE_IMAGE is the upstream base, already digest-pinned.
VLLM_IMAGE=${VLLM_IMAGE:-localhost/apollo-qwen38-lmcache@sha256:dce379422d6420af4006eacb78c677d2e864446858ab6b31b28aad2e86c6a7bf}
LMCACHE_IMAGE=${LMCACHE_IMAGE:-docker.io/vllm/vllm-openai@sha256:5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40}
VLLM_NAME=apollo-qwen38-vllm
LMCACHE_NAME=apollo-qwen38-lmcache

if [[ ${EUID} -eq 0 ]]; then SUDO=(); else SUDO=(sudo -n); fi
if [[ -z ${PODMAN_BIN:-} ]]; then
  for candidate in \
    /mnt/data/ai-evaluation/runtimes/tools/podman/bin/podman \
    /mnt/microvms/ai-evaluation/runtimes/tools/podman/bin/podman; do
    [[ -x $candidate ]] && PODMAN_BIN=$candidate && break
  done
fi
PODMAN_BIN=${PODMAN_BIN:-$(command -v podman)}
podman=("${SUDO[@]}" "$PODMAN_BIN" --root "$ROOT" --runroot "$RUNROOT")

GPU_DEVICES=(
  --device /dev/nvidia-modeset --device /dev/nvidia-uvm --device /dev/nvidia-uvm-tools
  --device /dev/nvidiactl --device /dev/nvidia0 --device /dev/nvidia1
  --device /dev/dri/card1 --device /dev/dri/renderD128
  --device /dev/dri/card2 --device /dev/dri/renderD129
)

preflight() {
  # The backend has no API credential: refuse a lost or bypassed firewall.
  local input_rules chain_rules
  input_rules=$("${SUDO[@]}" iptables -S INPUT)
  chain_rules=$("${SUDO[@]}" iptables -S apollo-qwen-personal)
  [[ $(sed -n '2p' <<<"$input_rules") == \
    '-A INPUT -d 100.77.14.27/32 -p tcp -m tcp --dport 11542 -j apollo-qwen-personal' ]]
  [[ $chain_rules == '-N apollo-qwen-personal
-A apollo-qwen-personal -i lo -j ACCEPT
-A apollo-qwen-personal -s 100.103.52.100/32 -i tailscale0 -j ACCEPT
-A apollo-qwen-personal -j REJECT --reject-with icmp-port-unreachable' ]]
  [[ $(findmnt -n -o SOURCE /mnt/data) == /dev/sdc1 ]]
  [[ $(nvidia-smi --query-gpu=uuid --format=csv,noheader -i 1) == GPU-af808452-3544-e84a-b33a-fc5588ede436 ]]
  [[ $(nvidia-smi --query-gpu=uuid --format=csv,noheader -i 2) == GPU-818843e6-ce30-5d98-a4b4-8d8580aaa049 ]]
  [[ -r $RUNTIME/serve-vllm.sh && -r $RUNTIME/serve-lmcache.sh && -r $MODEL_DIR/config.json ]]
}

stop() {
  "${podman[@]}" stop -t 30 "$VLLM_NAME" "$LMCACHE_NAME" >/dev/null 2>&1 || true
  "${podman[@]}" rm -f "$VLLM_NAME" "$LMCACHE_NAME" >/dev/null 2>&1 || true
}

run_lmcache() {
  "${podman[@]}" run "$1" --name "$LMCACHE_NAME" --pull=never --network=host \
    --user 1000:100 --cpus=4 --memory=12g --memory-swap=12g --shm-size=64m \
    --security-opt=no-new-privileges "${GPU_DEVICES[@]}" \
    -v "$RUNTIME:/trial:ro" \
    --entrypoint /bin/bash "$LMCACHE_IMAGE" /trial/serve-lmcache.sh
}

run_vllm() {
  "${podman[@]}" run "$1" --name "$VLLM_NAME" --pull=never --network=host \
    --user 1000:100 --cpus=8 --memory=80g --memory-swap=80g --shm-size=64m \
    --security-opt=no-new-privileges "${GPU_DEVICES[@]}" \
    -v "$MODEL_DIR:/model:ro" -v "$RUNTIME:/trial:ro" -v "$CACHE_DIR:/cache" \
    --entrypoint /bin/bash "$VLLM_IMAGE" /trial/serve-vllm.sh
}

# start: create both containers detached and return (manual use / smoke).
start() {
  preflight
  stop
  run_lmcache -d
  run_vllm -d
}

# run: supervisor mode for systemd -- either child exiting ends the unit.
run() {
  preflight
  stop
  local lmcache vllm
  run_lmcache --rm &
  lmcache=$!
  run_vllm --rm &
  vllm=$!
  wait -n "$lmcache" "$vllm" || true
  stop
  exit 1
}

case ${1:-start} in
  preflight) preflight ;;
  start) start ;;
  run) run ;;
  down) stop ;;
  status)
    "${podman[@]}" ps -a --filter name=apollo-qwen38 \
      --format '{{.Names}} {{.Image}} {{.Status}}'
    ;;
  *)
    echo "usage: $0 {start|run|down|status|preflight}" >&2
    exit 2
    ;;
esac
