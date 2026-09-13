# Portable compressed-KV reference

The first slice implements the integer packing and normalized segment/LUT
invariants in [test-contract.md](test-contract.md). It is a CPU mathematical
reference. It does not fit codebooks, load models, measure quality, implement
attention kernels, or establish an INT4/NVFP4 performance gain.

The [broader feature](compressed-kv.feature) remains unautomated. Only the bounded
CKV-02/CKV-04 subset described in the test contract has runnable checks.

```sh
python3 -B -m unittest -v test_reference
python3 -B reference.py
```

The second command prints deterministic synthetic checks as JSON. Packed fields
are unsigned 1–16-bit codes in this reference's little-endian convention, not a
signed INT4 tensor-core format. Segment scores match reconstructed keys, not
uncompressed model keys. Python floating-point reference tolerances are not GPU
precision acceptance thresholds.

## Bounded Orion execution

Build an archive containing only `reference.py`, `test_reference.py`,
`test-contract.md`, `run-cpu.sh`, and this README. Record SHA256 for the archive and
each source file. Transfer that archive to a unique trial directory on Orion,
verify its hash there and extract it without modifying the running service code.
Use the same archived source for any later Apollo CPU comparison.

Orion's observed interpreter on September 13 is
`/nix/store/7jydrdpxlsqjdlrxxxy15i5vw4bad4rs-python3-3.13.15/bin/python3`.
It requires no NumPy, GPU runtime, environment installation or model download.
From the extracted trial directory:

```sh
timeout 120s taskset -c 0 nice -n 10 bash run-cpu.sh \
  /nix/store/7jydrdpxlsqjdlrxxxy15i5vw4bad4rs-python3-3.13.15/bin/python3 \
  > receipt.json 2> checks.log
```

The outer command limits the entire run to 120 seconds and one CPU; the script
limits address space to 512 MiB. Preserve stdout, stderr and exit status. Check
chat and CPU retrieval health before and after. Never stop their services for
this CPU slice. A nonzero exit is a failed check, not a scientific negative.

The temporary source and receipts can remain for replay; no service, timer or
GPU allocation is created. Delete only the identified trial directory if cleanup
is requested. The original human brief and earlier Homelab plan were absent from
both checkouts during this slice; their bodies have not been reconstructed.

## Real-cache scalar diagnostic

The successor section of [test-contract.md](test-contract.md) covers
`scalar_probe.py` and `test_scalar_probe.py`. It uses Orion's existing Qwen3-4B
snapshot and cached CPU-capable runtime. Each of six fixed synthetic prompts gets
12 independent reconstructed-prefix comparisons: 2/4/8-bit scalar and BF16,
each K-only, V-only and combined. Weights and actual attention stay FP32 on CPU.
The BF16 control only rounds the prefix cache. These are diagnostic logit/error
measurements, not generation quality or native INT4 throughput.

Use an immutable source archive containing both Python modules, the two reference
modules, this README and the test contract. Verify its SHA256 after copying it to
a unique Orion trial directory. Create its `out/` directory, then run there:

```sh
podman run --rm --pull=never --network=none --read-only \
  --cpus=4 --memory=32g --memory-swap=32g --pids-limit=256 --timeout=1200 \
  --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=256m \
  -e OMP_NUM_THREADS=4 -e MKL_NUM_THREADS=4 -e OPENBLAS_NUM_THREADS=4 \
  -e USE_TF=0 -e USE_FLAX=0 -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD:/work:ro" -v "$PWD/out:/out:rw" -w /work \
  -v /projects/rocmvenv/lib/python3.12/site-packages:/host-deps:ro \
  -v /projects/hf/hub/models--Qwen--Qwen3-4B:/model:ro \
  a955de5bbe923a946401a55e1ff138cca039e0f0fc0e4ea1938be11e4480bb6f \
  bash -c 'python -B -m unittest -v test_reference test_scalar_probe >&2 && python -B -u scalar_probe.py' \
  > results.jsonl 2> checks.log
```

Record exit status separately, preserving failures. Save the exact image ID,
model snapshot and file hashes, dependency versions/source hashes, source archive
and before/after service start times and health. The installed host dependencies
are mounted read-only but are not a published immutable environment: compare their
recorded identities before replay. Do not use `PYTHONPATH` to put host dependencies
ahead of the image's PyTorch; the modules append `/host-deps` after normal imports.

No GPU devices or network are exposed, no packages are installed, and no service
is stopped. Six lossless NumPy cache captures fit within a 128 MiB raw tensor
budget; their hashes accompany the JSONL receipts. Capture files hold model
activations from public synthetic prompts, not user conversations. They are useful
for replay diagnostics; disjoint calibration/evaluation data and task-quality
tests remain separate work. Cache append semantics follow the installed Qwen3
source, checked against [Transformers' cache explanation](https://huggingface.co/docs/transformers/main/en/cache_explanation).
