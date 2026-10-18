# Apollo Qwen3.8-27B Gittensor + LMCache trial

This is a loopback-only, task-owned Podman trial on Apollo. It does not replace
the personal runtime or install a service. The launcher builds a local image
from pinned `vllm/vllm-openai:v0.30.0` digest
`5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40`.
The launcher checks the three model shard hashes in `model-sha256.txt` before
starting. The model is Gittensor revision
`0cc27958cefbbe231782ec8511de8c4eb5233348` at
`/mnt/data/ai/models/gittensor/Qwen3.8-27B-NVFP4-RTX5090-0cc2795`.

`lmcache-packed-kv.patch` is the code portion of [LMCache PR 4731](https://github.com/LMCache/LMCache/pull/4731).
`apollo-packed-kv-layout.patch` additionally handles the K/V-first strided
attention view observed with vLLM 0.30 on Apollo. `test_packed_kv.py` fails on
the unpatched image and passes on the derived image. The patches remain local
trial inputs; publish and pin a reviewed image before any deployment.

## Reproduce

Copy this directory to `/mnt/data/ai/validation/20260922-qwen38-repro` on
Apollo, create its `cache/` and `results/` directories, then run
`bash run-lmcache.sh` there. The script checks the two 5060 Ti UUIDs, model
shards, task container names, and trial ports. It builds the image without
pulling, then waits for the LMCache server and vLLM health checks. The API
binds `127.0.0.1:11542`; LMCache ZMQ and HTTP bind `127.0.0.1:11550` and
`:11552` (metrics are served on the HTTP port; the `--prometheus-port 11551`
flag is dead in this build).

`run-lmcache.sh` is parameterized by `MAX_MODEL_LEN` (default 262144),
`GPU_UTIL` (0.94), `L1_GB` (8), `SERVER_NAME` and `ENGINE_NAME`. Its LMCache
server container memory limit is `L1_GB + 4` GiB so a large L1 is not
OOM-killed. `run-long-restore.sh` drives one bounded stage end to end: it runs
the launcher, then `podman exec`s `probe_restore.py` inside the engine. The
probe runs a cold needle, clears only vLLM's native prefix cache
(`POST /reset_prefix_cache`), re-runs the identical prompt, and asserts
external L1 hit tokens, completed retrieves, and the correct marker.

The default profile uses tensor parallelism 2, MTP 6, FP8 KV, 262,144 max
context, 864-token LMCache chunks, `align` Mamba cache, 1,727 scheduler tokens,
94% GPU allocation, and 8 GiB CPU L1. No CUDA expandable-segments setting:
vLLM rejects it with this KV connector unless its CuMem allocator is enabled.
The [Qwen hybrid recipe](https://docs.lmcache.ai/recipes/qwen3_5.html) explains
the block, Mamba, and scheduler settings.

Use the Podman executable with root
`/mnt/microvms/ai/cache/podman-root` and runroot
`/run/apollo-ftw-containers` for status and logs. Stop both task containers
with `podman stop -t 30 apollo-qwen38-gittensor-lmcache
apollo-qwen38-lmcache-server`; inspect logs before removing them. A failed
engine can leave GPU memory registered in the cache server; stop the server to
release it before another engine trial. Keep model, cache, and results files.

## Observed gates

| Gate | Result on Apollo, 2026-09-23 UTC |
|---|---|
| Stock Gittensor, no LMCache | 262K context, 272,533-token KV pool, 249,039-token needle recall passed. |
| LMCache packed pages | Stock LMCache treated attention as 32-slot compressed pages; patched image registers all 17 attention layers as 864-slot pages. |
| 8 GiB L1, 94% GPU | 26,033-token cold needle passed, TTFT 15.647 s. After vLLM-only prefix reset, same prompt and answer passed with 25,920 external hit tokens and TTFT 0.550 s. |
| 40 GiB L1 at 262K | Superseded: 94% GPU hit CUDA OOM in startup; 92% could not allocate 262K KV; 93.3% started with 263,742 KV tokens but hit CUDA OOM near the end of a 120K cold prefill. |
| 200K context / 100K retrieval | 16 GiB L1 at 90% GPU: 100,034-token cold needle TTFT 84.833 s; after vLLM-only prefix reset, 99,360 external L1 hit tokens, two completes, marker correct, TTFT 1.462 s. `LMCACHE_LONG_RESTORE_OK`. |
| 240K context / 120K retrieval | 20 GiB L1 at 92% GPU: 120,026-token cold needle TTFT 109.817 s; after reset, 119,232 external L1 hit tokens, two completes, marker correct, TTFT 1.867 s. `LMCACHE_LONG_RESTORE_OK`. |

Receipts: `/mnt/data/ai/validation/20260922-qwen38-repro/results/` on Apollo,
including `gittensor-lmcache-cold-26k.json`,
`gittensor-lmcache-restored-26k.json`, `gittensor-lmcache-200k-100k-probe.json`,
`gittensor-lmcache-240k-120k-probe.json`, the matching `campaign-*` and
`*-startup.log` files, metric snapshots, and the 40 GiB failure logs. The 26K
store used 3.45 GB of L1 (~132.5 KB/token); 8 GiB cannot hold a full-context
prefix, and the long stages size L1 to the retrieval. Image/video KV reuse and
L2 persistence are unvalidated. The paired Gittensor/Unsloth accuracy
evaluation is the final step after implementation review.
