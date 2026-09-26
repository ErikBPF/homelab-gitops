#!/usr/bin/env bash
set -euo pipefail
# The backend has no API credential; refuse a lost or bypassed firewall.
input_rules=$(sudo -n iptables -S INPUT)
chain_rules=$(sudo -n iptables -S apollo-qwen-personal)
[[ $(sed -n '2p' <<<"$input_rules") == '-A INPUT -d 100.77.14.27/32 -p tcp -m tcp --dport 11542 -j apollo-qwen-personal' ]]
[[ $chain_rules == '-N apollo-qwen-personal
-A apollo-qwen-personal -i lo -j ACCEPT
-A apollo-qwen-personal -s 100.103.52.100/32 -i tailscale0 -j ACCEPT
-A apollo-qwen-personal -j REJECT --reject-with icmp-port-unreachable' ]]
trial=/mnt/microvms/ai/validation/20260910-qwen27b-personal
podman=(sudo -n /mnt/microvms/ai-evaluation/runtimes/tools/podman/bin/podman --root /mnt/microvms/ai/cache/podman-root --runroot /run/apollo-ftw-containers)
[[ $(findmnt -n -o SOURCE /mnt/microvms) == /dev/md127 ]]
[[ $(nvidia-smi --query-gpu=uuid --format=csv,noheader -i 1) == GPU-af808452-3544-e84a-b33a-fc5588ede436 ]]
[[ $(nvidia-smi --query-gpu=uuid --format=csv,noheader -i 2) == GPU-818843e6-ce30-5d98-a4b4-8d8580aaa049 ]]
root=$(cat "$trial/vllm-dependency-root.txt")
"${podman[@]}" run -d --name apollo-qwen27b-personal --restart on-failure:3 --pull=never --network=host --device nvidia.com/gpu=1 --device nvidia.com/gpu=2 --user 1000:100 --cpus=8 --memory=80g --memory-swap=80g --shm-size=2g --cap-drop=ALL --security-opt=no-new-privileges -v "$root/usr/local:/usr/local:ro" -v "$trial:/trial" -v /mnt/microvms/ai/validation/20260909-vllm-dual/site:/site:ro -v /mnt/microvms/ai/models/nvidia/Qwen3.8-27B-NVFP4:/model:ro -v /mnt/microvms/ai/cache/vllm:/cache -e PYTHONPATH=/site -e HF_HOME=/cache/user1000/hf -e XDG_CACHE_HOME=/cache/user1000 -e TORCH_HOME=/cache/user1000/torch -e CUDA_CACHE_PATH=/cache/user1000/cuda -e TMPDIR=/trial/tmp -e OMP_NUM_THREADS=4 -e HF_HUB_OFFLINE=1 -e NCCL_P2P_DISABLE=1 --entrypoint /bin/bash localhost/apollo-ftw-base:v0.28.0 /trial/supervise.sh
