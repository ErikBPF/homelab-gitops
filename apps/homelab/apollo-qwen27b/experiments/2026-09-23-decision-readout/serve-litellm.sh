#!/usr/bin/env bash
# Bring the Gittensor engine + decision sidecar up for LiteLLM on Apollo.
#
# Binds vLLM and the sidecar to the Apollo tailnet IP and installs a runtime
# iptables rule that admits only discovery (100.103.52.100). The durable rule
# belongs in desktop-nixos; this is the runtime equivalent of the earlier
# apollo-qwen-personal precedent.
set -euo pipefail

trial=/mnt/data/ai/validation/20260922-qwen38-repro
model=/mnt/data/ai/models/gittensor/Qwen3.8-27B-NVFP4-RTX5090-0cc2795
base=docker.io/vllm/vllm-openai@sha256:5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40
image=localhost/apollo-qwen38-lmcache:trial
host=100.77.14.27
peer=100.103.52.100
served_name=qwen38-27b-nvfp4
sidecar_port=11560
max_model_len=${MAX_MODEL_LEN:-200000}
gpu_util=${GPU_UTIL:-0.90}
l1_gb=${L1_GB:-8}
server_mem=$((l1_gb + 4))g
server_name=apollo-qwen38-lmcache-server
engine_name=apollo-qwen38-gittensor-lmcache
chain=apollo-qwen-litellm
podman=(sudo -n /mnt/microvms/ai-evaluation/runtimes/tools/podman/bin/podman
    --root /mnt/microvms/ai/cache/podman-root --runroot /run/apollo-ftw-containers)

[[ -f "$model/config.json" && -f "$trial/Containerfile.lmcache" && -d "$trial/cache" && -f "$trial/decision_sidecar.py" ]]
(
    cd "$model"
    sha256sum --check "$trial/model-sha256.txt"
)
[[ $(nvidia-smi --query-gpu=uuid --format=csv,noheader -i 0) == GPU-818843e6-ce30-5d98-a4b4-8d8580aaa049 ]]
[[ $(nvidia-smi --query-gpu=uuid --format=csv,noheader -i 1) == GPU-af808452-3544-e84a-b33a-fc5588ede436 ]]
! "${podman[@]}" container exists "$server_name"
! "${podman[@]}" container exists "$engine_name"
if [[ -n $(ss -H -ltn '( sport = :11542 or sport = :11550 or sport = :11551 or sport = :11552 or sport = :11560 )') ]]; then
    echo 'Trial ports 11542/11550/11551/11552/11560 must be free' >&2
    exit 1
fi

# Runtime firewall: exactly one jump per port into a chain that admits loopback
# and the discovery peer, then rejects. Refuses a broadened or reordered INPUT.
setup_firewall() {
    sudo -n iptables -N "$chain" 2>/dev/null || true
    sudo -n iptables -F "$chain"
    sudo -n iptables -A "$chain" -i lo -j ACCEPT
    sudo -n iptables -A "$chain" -s "$peer/32" -i tailscale0 -p tcp -m multiport --dports 11542,"$sidecar_port" -j ACCEPT
    sudo -n iptables -A "$chain" -j REJECT --reject-with icmp-port-unreachable
    for port in "$sidecar_port" 11542; do
        sudo -n iptables -C INPUT -d "$host/32" -p tcp -m tcp --dport "$port" -j "$chain" 2>/dev/null ||
            sudo -n iptables -I INPUT 1 -d "$host/32" -p tcp -m tcp --dport "$port" -j "$chain"
    done
}
setup_firewall

expected_chain="-N $chain
-A $chain -i lo -j ACCEPT
-A $chain -s $peer/32 -i tailscale0 -p tcp -m multiport --dports 11542,$sidecar_port -j ACCEPT
-A $chain -j REJECT --reject-with icmp-port-unreachable"
[[ $(sudo -n iptables -S "$chain") == "$expected_chain" ]] || { echo 'firewall chain mismatch' >&2; exit 1; }
input_rules=$(sudo -n iptables -S INPUT)
grep -Fxq -- "-A INPUT -d $host/32 -p tcp -m tcp --dport $sidecar_port -j $chain" <<<"$input_rules" || { echo 'sidecar jump missing' >&2; exit 1; }
grep -Fxq -- "-A INPUT -d $host/32 -p tcp -m tcp --dport 11542 -j $chain" <<<"$input_rules" || { echo 'engine jump missing' >&2; exit 1; }

"${podman[@]}" build --pull=never -t "$image" -f "$trial/Containerfile.lmcache" "$trial"
"${podman[@]}" run -d --name "$server_name" --pull=never \
    --network=host --ipc=host --device nvidia.com/gpu=0 --device nvidia.com/gpu=1 \
    --user 1000:100 --cpus=4 --memory="$server_mem" --memory-swap="$server_mem" \
    --cap-drop=ALL --security-opt=no-new-privileges \
    -e LMCACHE_TRACK_USAGE=false --entrypoint /usr/local/bin/lmcache "$base" \
    server --host 127.0.0.1 --port 11550 --http-host 127.0.0.1 --http-port 11552 \
    --prometheus-port 11551 --chunk-size 864 --separate-object-groups \
    --l1-size-gb "$l1_gb" --l1-init-size-gb "$l1_gb" --eviction-policy LRU \
    --max-gpu-workers 2 --max-cpu-workers 2

for attempt in {1..30}; do
    if [[ $("${podman[@]}" inspect -f '{{.State.Running}}' "$server_name") != true ]]; then
        echo 'LMCache server exited before its health check' >&2
        exit 1
    fi
    if curl -fsS --max-time 2 http://127.0.0.1:11552/healthcheck >/dev/null 2>&1; then
        break
    fi
    if (( attempt == 30 )); then
        echo 'LMCache server failed its health check' >&2
        exit 1
    fi
    sleep 1
done

"${podman[@]}" run -d --name "$engine_name" --pull=never \
    --network=host --ipc=host --device nvidia.com/gpu=0 --device nvidia.com/gpu=1 \
    --user 1000:100 --cpus=8 --memory=80g --memory-swap=80g \
    --cap-drop=ALL --security-opt=no-new-privileges \
    -v "$model:/model:ro" -v "$trial:/trial:ro" -v "$trial/cache:/cache:rw" \
    -e HOME=/cache -e HF_HOME=/cache/hf -e XDG_CACHE_HOME=/cache/xdg \
    -e VLLM_CACHE_ROOT=/cache/vllm -e TORCH_HOME=/cache/torch -e CUDA_CACHE_PATH=/cache/cuda \
    -e HF_HUB_OFFLINE=1 -e NCCL_P2P_DISABLE=1 -e VLLM_ALLOW_LONG_MAX_MODEL_LEN=1 \
    -e VLLM_FLASHINFER_WORKSPACE_BUFFER_SIZE=67108864 \
    -e VLLM_SERVER_DEV_MODE=1 -e LMCACHE_TRACK_USAGE=false "$image" \
    --model /model --served-model-name "$served_name" --host "$host" --port 11542 \
    --tensor-parallel-size 2 --no-enable-flashinfer-autotune --disable_custom_all_reduce \
    --load-format ipc_cache --trust-remote-code --kv-cache-dtype fp8 --max-model-len "$max_model_len" \
    --max-num-seqs 2 --compilation-config '{"cudagraph_mode":"PIECEWISE"}' \
    --mamba-cache-dtype bfloat16 --mamba-ssm-cache-dtype bfloat16 \
    --gpu_memory_utilization "$gpu_util" --max-num-batched-tokens 1727 \
    --enable-prefix-caching --enable-chunked-prefill \
    --limit-mm-per-prompt '{"video":0}' --mm-processor-kwargs '{"max_pixels":1000000}' \
    --speculative-config '{"method":"mtp","num_speculative_tokens":6}' \
    --reasoning-parser qwen3 --tool-call-parser qwen3_xml --enable-auto-tool-choice \
    --default-chat-template-kwargs '{"preserve_thinking":true}' --mamba-cache-mode align \
    --kv-transfer-config '{"kv_connector":"LMCacheMPConnector","kv_connector_module_path":"lmcache.integration.vllm.lmcache_mp_connector","kv_role":"kv_both","kv_connector_extra_config":{"lmcache.mp.host":"127.0.0.1","lmcache.mp.port":11550,"lmcache.mp.autostart":false}}'

for attempt in {1..300}; do
    if [[ $("${podman[@]}" inspect -f '{{.State.Running}}' "$engine_name") != true ]]; then
        echo 'vLLM exited before its health check' >&2
        exit 1
    fi
    if curl -fsS --max-time 2 "http://$host:11542/health" >/dev/null 2>&1; then
        break
    fi
    if (( attempt == 300 )); then
        echo 'vLLM failed its health check' >&2
        exit 1
    fi
    sleep 1
done

"${podman[@]}" exec -d "$engine_name" env PYTHONDONTWRITEBYTECODE=1 \
    python3 /trial/decision_sidecar.py --vllm-url "http://$host:11542" \
    --model "$served_name" --host "$host" --port "$sidecar_port"

for attempt in {1..30}; do
    if curl -fsS --max-time 2 "http://$host:$sidecar_port/healthz" >/dev/null 2>&1; then
        exit 0
    fi
    if (( attempt == 30 )); then
        echo 'decision sidecar failed its health check' >&2
        exit 1
    fi
    sleep 1
done
