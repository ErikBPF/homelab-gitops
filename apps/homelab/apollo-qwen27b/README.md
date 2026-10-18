# Apollo Qwen3.8-27B NVFP4 runtime

Host Podman runtime for Apollo's local inference: **vLLM (TP=2 on the two RTX
5060 Ti) + LMCache in one container**, serving `Qwen3.8-27B-NVFP4`. The `pqnld`
decision sidecar is separate (host port 11560, in `dibuild`).

**This directory is the manual/host-side reference.** The deployed, reboot-durable
service is declared in `desktop-nixos` (Apollo host module), which wraps the same
recipe in a systemd unit.

## Verified configuration (Apollo, 2026-10-12)

| Setting | Value |
|---|---|
| model | `/mnt/data/ai/models/gittensor/Qwen3.8-27B-NVFP4-RTX5090-0cc2795` → `/model` |
| context | **105000** (probed ceiling with LMCache attached; 107500 fails) |
| parallel | `--max-num-seqs 4` (≥2 verified concurrently) |
| vLLM util | **0.80** — 0.90 leaves LMCache's KV registration no GPU headroom (`CUDA error: out of memory`) |
| KV dtype | fp8, `--enable-prefix-caching`, `--enable-chunked-prefill` |
| speculation | MTP, `num_speculative_tokens=6` |
| LMCache | chunk 864, `--separate-object-groups`, L1 8 GiB host RAM, LRU, 2 GPU / 2 CPU workers |

Two constraints worth keeping:
- **One container.** KV registration only completes when LMCache and vLLM share
  the CUDA/IPC namespace; as separate containers vLLM stalled on
  `register_kv_caches`.
- **Headroom for LMCache.** vLLM must not take the whole card, or LMCache's
  registration OOMs.

## Usage

```sh
sudo ./launch.sh start      # detached; manual use
sudo ./launch.sh status
sudo ./launch.sh down
sudo ./launch.sh run        # foreground supervisor mode for a systemd unit
```

`launch.sh` runs one container (`apollo-qwen38`) with a read-only model mount and
the patched image pinned by id (see `experiments/2026-09-22-reproduction/Containerfile.lmcache`;
a local build has no repository digest). `preflight` refuses to start unless:

- the INPUT jumps for `100.77.14.27:11542` and `:11560` target the
  `apollo-qwen-litellm` chain, and that chain is exactly loopback + Discovery
  (`100.103.52.100`, `tailscale0`, both ports) + REJECT;
- `/mnt/data` is the expected filesystem;
- both GPU UUIDs are present;
- the pinned image and the model/script paths exist.

The tailscale `ts-input` chain precedes those jumps and accepts `-i tailscale0`,
so the allowlist constrains non-tailscale sources only — tailnet-wide access is
the current operator policy.

## Layout

| What | Path |
|---|---|
| Podman root / runroot | `/mnt/data/ai/cache/podman-root` / `/run/apollo-ftw-containers` |
| Model | `/mnt/data/ai/models/gittensor/Qwen3.8-27B-NVFP4-RTX5090-0cc2795` |
| Cache | `/mnt/data/ai/cache` → `/cache` |
| Scripts | this directory → `/trial:ro` |

Research and benchmark artifacts live under [`experiments/`](experiments/).
