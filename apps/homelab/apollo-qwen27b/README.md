# Apollo Qwen3.8-27B NVFP4 runtime

Host Podman runtime for Apollo's local inference: **vLLM (TP=2 on the two RTX
5060 Ti)** serving `Qwen3.8-27B-NVFP4` with **LMCache** as the KV store, fronted
by the `pqnld` decision sidecar (separate, not managed here). This is a host
service, not an Argo application.

**Status:** configuration regenerated 2026-10-11 from the running containers
(`apollo-qwen38-vllm`, `apollo-qwen38-lmcache`). The previous scripts described a
different model path (`/mnt/microvms`), a different model (NVIDIA NVFP4), 90k
context, and a single-container supervisor; none of that matched the live stack.

## Layout

| What | Path |
|---|---|
| Podman root | `/mnt/data/ai/cache/podman-root` |
| Podman runroot | `/run/apollo-ftw-containers` |
| Model | `/mnt/data/ai/models/gittensor/Qwen3.8-27B-NVFP4-RTX5090-0cc2795` → `/model` |
| Cache | `/mnt/data/ai/cache` → `/cache` (vLLM/`HF_HOME`/XDG) |
| Scripts (host) | `/etc/apollo-ai/` → mounted read-only at `/trial` |

## Pinned images

- `VLLM_IMAGE` — `localhost/apollo-qwen38-lmcache@sha256:dce3794…` (image id).
  Built from [`experiments/2026-09-22-reproduction/Containerfile.lmcache`](experiments/2026-09-22-reproduction/Containerfile.lmcache)
  (upstream `vllm/vllm-openai` + LMCache PR 4731 packed-KV patches). Rebuild and
  re-pin the id whenever that Containerfile changes.
- `LMCACHE_IMAGE` — `docker.io/vllm/vllm-openai@sha256:5f5e535…` (upstream base).

Both containers run `--pull=never`, `--network=host`, `--user 1000:100`,
`--security-opt=no-new-privileges`, and the explicit NVIDIA/DRI device list.

## Runtime configuration

`serve-vllm.sh`: `--max-model-len 200000`, `--max-num-seqs 8`,
`--kv-cache-dtype fp8`, `--max-num-batched-tokens 1727`,
`--gpu-memory-utilization 0.90`, `--load-format ipc_cache`, MTP
`num_speculative_tokens=6`, `qwen3_xml` tool parser, `preserve_thinking`,
LMCache connector to `127.0.0.1:11550` (no autostart).

`serve-lmcache.sh`: `--chunk-size 864 --separate-object-groups --l1-size-gb 8
--l1-init-size-gb 8 --eviction-policy LRU --max-gpu-workers 2 --max-cpu-workers 2`
(prometheus 11551, HTTP 11552).

## Install

On Apollo:

```sh
sudo ./install.sh            # /etc/apollo-ai + /etc/systemd/system/apollo-ai.service, enable --now
sudo ./install.sh --no-start # install + enable only
```

`launch.sh` keeps the original firewall preflight: it refuses to start unless the
INPUT jump for `100.77.14.27:11542` is the first rule and the
`apollo-qwen-personal` chain is exactly loopback + Discovery (`100.103.52.100`,
`tailscale0`) + REJECT. It also asserts the `/mnt/data` filesystem, both GPU UUIDs,
and the model/script paths.

`apollo-ai.service` runs `launch.sh run` in the foreground with
`Restart=always`; if either container exits, the whole runtime restarts. This is
the reboot-durable path that the previous single-container `supervise.sh` did not
provide.

## Operations

```sh
systemctl status apollo-ai
journalctl -u apollo-ai -f
curl -s http://100.77.14.27:11542/health
/etc/apollo-ai/launch.sh status
```

## Remaining gaps (promotion)

- The unit and scripts are installed imperatively into `/etc`; a declarative
  NixOS unit in `desktop-nixos` (next to `apollo-ai-storage.service`) is the
  promotion target.
- The patched vLLM image is a local build pinned by image id; promotion needs it
  pushed and pulled by digest from a registry.
- The firewall rules remain operator-managed; only the preflight is automated.

Research and benchmark artifacts live under [`experiments/`](experiments/).
