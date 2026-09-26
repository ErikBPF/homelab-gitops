# Apollo personal Qwen27B runtime

User-authorized running experiment, 2026-09-10. This is a host Podman runtime,
not an Argo application or a reboot-qualified deployment.

Runtime directory: `/mnt/microvms/ai/validation/20260910-qwen27b-personal`.
Container: `apollo-qwen27b-personal`. Copy these four scripts into the runtime
directory and run `bash launch.sh` on Apollo after the prerequisites below.
No expiry timer; supervisor stops both children if either exits, and Podman
retries failure up to three times. Model: Qwen3.8-27B NVIDIA NVFP4, MTP2,
90,000 context, FP8 KV, batch 1600; two 5060 Tis, eight CPUs, 80 GiB/no swap.
LMCache uses eight GiB host RAM. RTX 3070 unused.

Prerequisites retained from the verified capacity trial:

- Copy its isolated `lmcache-site` directory and create `tmp` here. The source
  trial is `/mnt/microvms/ai/validation/20260910-qwen27b-lmcache-capacity`.
- Mount the existing `apollo-vllm-nightly` dependency container with the same
  Podman root/runroot as `launch.sh`; write its path to `vllm-dependency-root.txt`.
  Shared vLLM source and downloaded model files remain read-only mounts.
- Restrict the API before launch: an INPUT jump for destination
  `100.77.14.27:11542` must be the first INPUT rule and leads to chain `apollo-qwen-personal`. That chain accepts
  loopback, accepts source `100.103.52.100` on `tailscale0`, then rejects all
  other traffic. No public/LAN bind. Tailscale authenticates Discovery;
  no reusable backend secret is created. LMCache stays on localhost:11550. The launch script checks the exact jump and
  ordered allowlist before starting the container; it refuses missing, unreadable,
  or broadened rules. This preflight does not monitor later firewall changes.

Logs: `vllm.log`, `lmcache.log`. Health: `http://100.77.14.27:11542/health`
from Apollo or Discovery. Existing developer reset endpoint is restricted by
the same network rule and used only for the validation probe.

Stop with the Podman executable/root/runroot in `launch.sh`:
`stop -t 30 apollo-qwen27b-personal`. Remove only the matching INPUT jump and
owned firewall chain after stopping. Unmount the dependency container only
after all consumers are stopped. Keep model/cache data.

The mounted dependency root and runtime firewall rules are not reboot durable;
host reboot or firewall reload needs explicit recovery before exposing the API.
Promotion requires packaging the patched dependencies and declarative host
lifecycle/network policy. Do not claim this experiment provides that promotion.
