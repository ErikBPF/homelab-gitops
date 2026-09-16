# Portable compressed-KV reference

The first slice implements the integer packing and normalized segment/LUT
invariants in [test-contract.md](test-contract.md). It is a CPU mathematical
reference. It does not fit codebooks, load models, measure quality, implement
attention kernels, or establish an INT4/NVFP4 performance gain.

The [broader feature](compressed-kv.feature) remains unautomated. The reference and
scalar diagnostic have only the bounded runnable coverage in the test contract.

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

`stored_bytes` counts tensor payload, FP32 scales and untouched FP32 cache sides.
It excludes packet metadata, allocator alignment and capture-file overhead.
`baseline_bf16_bytes` is a logical size comparison, not a BF16 model execution.

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

## Frozen dictionary diagnostic

The next contract section binds `dictionary_probe.py`, `test_dictionary_probe.py`
and the immutable `dictionary-prompts.json` manifest. Include those three files
alongside the scalar/reference modules, contract and README in the source archive.
Use the same offline Orion container recipe and mounts above, replacing its
`bash -c` body with:

```sh
python -B -m unittest -v test_reference test_scalar_probe test_dictionary_probe >&2 && python -B -u dictionary_probe.py
```

The run captures 18 new documents, six per declared split, including final-token
Q and full input K/V. QK probabilities must match the actual eager attention row
before the capture is graded. Three fit seeds compare global K/V spherical VQ
against greedily paired fixed endpoints with eight interpolation levels. This
segment heuristic does not jointly optimize endpoints; that variant remains
separate work. Calibration alone fits tables; selection and evaluation receive
separate records under frozen parameters. All 288 comparisons run despite losses.

Each direction code plus BF16 radius costs three bytes per eight components.
Report raw table costs and a common padded allocation separately: per compressed
side VQ stores 8192 bytes, segments store 2048 plus 6144 padding. Scalar-2 has the
same payload rate and receives 8192 comparison padding; scalar-4 is a higher-rate
control. Padding is not an efficiency gain. Expanded direction tables and
reconstructed FP32 attention remain working storage, separate from encoded bytes.

Results measure reconstruction and conditional attention-output error with frozen
original Q. They do not propagate errors through layers or measure generation,
append, transport, GPU kernels or task quality. The original six scalar prompts
remain replay fixtures and do not enter these fitting/evaluation splits. The
four-CPU, 32-GiB, 20-minute trial retains at most 1 GiB of source, captures, tables
and receipts under its unique output directory; preserve them by artifact hash
after checking service health. No new dependency or host configuration is needed.

## Joint endpoint replay

Include `joint_segment_probe.py` and `test_joint_segment_probe.py` with the previous
source archive contents. Verify retained dictionary bundle
`73465d6f361dff338674c1a9d9a293778918f846fabdcfc1bf0d8cbb3cbdc889.tar`
from `/projects/compressed-kv/artifacts/` against its filename hash before extracting
it into the new trial's `input/` directory. Create a separate `out/` directory.
From that trial directory, run:

```sh
podman run --rm --pull=never --network=none --read-only \
  --cpus=4 --memory=4g --memory-swap=4g --pids-limit=256 --timeout=1200 \
  --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m \
  -e OMP_NUM_THREADS=4 -e MKL_NUM_THREADS=4 -e OPENBLAS_NUM_THREADS=4 \
  -e USE_TF=0 -e USE_FLAX=0 -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD:/work:ro" -v "$PWD/input:/input:ro" -v "$PWD/out:/out:rw" -w /work \
  -v /projects/rocmvenv/lib/python3.12/site-packages:/host-deps:ro \
  a955de5bbe923a946401a55e1ff138cca039e0f0fc0e4ea1938be11e4480bb6f \
  bash -c 'python -B -m unittest -v test_reference test_scalar_probe test_dictionary_probe test_joint_segment_probe >&2 && python -B -u joint_segment_probe.py' \
  > results.jsonl 2> checks.log
```

This model-free run reuses exact calibration samples and all three initializers.
It fits endpoints for 50 Adam steps at 0.03, retaining the final iterate and all
calibration losses. All 288 baseline comparisons must reproduce before scoring
108 refined-segment cases. Viewed evaluation fixtures remain paired diagnostics.
An improvement in direction loss need not improve attention error. New outputs
have a 64-MiB budget; preserve input/output hashes and service health as before.
No model mount, weight loading, recapture or package installation is needed.


## Cartesian corrective validation

The learned spherical runs above are historical diagnostics. They used normalized
vectors plus radii; none implemented logarithmic encoding. The corrective contract
runs Euclidean VQ, fixed affine segments and jointly optimized affine segments on
the original Cartesian captures. Keep the model's RoPE unchanged. No normalization,
radius field or logarithm belongs to these new codecs.

Archive `cartesian_probe.py` and `test_cartesian_probe.py` alongside all previous
Python modules/tests, fixed prompts, README and test contract. Verify source hashes
before execution. Extract the verified dictionary bundle
`73465d6f361dff338674c1a9d9a293778918f846fabdcfc1bf0d8cbb3cbdc889.tar`
into `input/` and the verified joint bundle
`638ddc8c6821a74f97b818406fff0f854a74d2af8c568a7c13527d61a20a8f4b.tar`
into `joint/`; both reside under Orion's `/projects/compressed-kv/artifacts/`.
Create a new empty `out/`. Use the joint replay recipe's immutable image and
four-CPU/4-GiB limits, adding `-v "$PWD/joint:/joint:ro"` and replacing its command:

```sh
python -B -m unittest -v test_reference test_scalar_probe test_dictionary_probe test_joint_segment_probe test_cartesian_probe >&2 && python -B -u cartesian_probe.py
```

This runs 396 historical baseline comparisons, 324 Cartesian learned comparisons
and 72 additional scalar-8/BF16 frozen-Q controls: 792 rows. Every seed and loss is
retained. The new learned codecs store one byte per eight components, compared
with three for the spherical code/radius representation. Report actual raw and
padded bytes; these cross-rate results cannot isolate a coordinate effect.
LUT checks establish numerical equivalence only, not kernel performance.

Separately rerun the original scalar diagnostic recipe with its original immutable
source archive from bundle
`6802494af35a47d2b9c0ba658673797f06cf70c50175f39c92eb45a7efd47a3e.tar`.
Those 72 prefix-logit comparisons were already Cartesian, and require the cached
model for an exact replay. Preserve their original metrics and compare receipts;
do not substitute frozen-Q attention for propagated logit error. This separate
trial uses the existing 32-GiB model limit and output budget. Verify live container
limits, source/dependency/input hashes and unchanged services for both runs, then
preserve each evidence bundle by SHA256. No new downloads, GPU access or service
changes are needed. Logarithmic encoding remains a separate future experiment.

## Matched logarithmic grids and teacher-forced propagation

Archive `log_probe.py`, `teacher_forced_probe.py` and their two test modules with
all previous source files. This continuation keeps Cartesian axes and group32
scales fixed. Signed logarithmic magnitudes span eight octaves, with explicit zero;
2/4/8-bit rates match the existing linear controls exactly. Log-2 and linear-2
have identical grids. Attention still operates on reconstructed FP32 values.

Verify and extract the dictionary bundle
`73465d6f361dff338674c1a9d9a293778918f846fabdcfc1bf0d8cbb3cbdc889.tar`
into `input/` and the Cartesian bundle
`a6966f9bc5951acbbb8aaea7ab6bc237e7651b59734c12b9a9f290a6f57d7e04.tar`
into `cartesian/`. Both reside under `/projects/compressed-kv/artifacts/` on Orion.
Use the existing offline four-CPU/4-GiB recipe, with read-only mounts at `/input`
and `/cartesian`. The model-free command is:

```sh
python -B -m unittest -v test_reference test_scalar_probe test_dictionary_probe test_joint_segment_probe test_cartesian_probe test_log_probe test_teacher_forced_probe >&2 && python -B -u log_probe.py --input-dir /input --cartesian-dir /cartesian
```

Retain all 252 frozen-Q rows: 144 linear/BF16 controls and 108 logarithmic cases.
The new scalar comparisons have no artificial table padding. Replay checks compare
actual payload/scale/untouched bytes and metrics with retained controls.

For propagation, use the same source/input archive and cached image with the
existing read-only Qwen3 model mount and 32-GiB/no-swap memory limit. Each variant
quantizes newly produced K/V before attention from the first prefill onward.
Previously cached entries are never quantized again. Full causal teacher forcing
is checked against prefill-plus-append execution in the tiny-model tests.
Run two separate three-document shards, each with the 20-minute deadline:

```sh
python -B -u teacher_forced_probe.py --input-dir /input --cartesian-dir /cartesian --document-ids evaluation_basalt evaluation_bicycle_gears evaluation_beekeeping
python -B -u teacher_forced_probe.py --input-dir /input --cartesian-dir /cartesian --document-ids evaluation_orchestra evaluation_recursion evaluation_javascript_map
```

Each command runs in its own owned container/trial directory; these are not two
commands to append blindly to a single bounded container. Across both shards,
288 tracks score all 189 target positions for 9072 variant-position observations.
Preserve per-position KL, target NLL, logit error, top-token agreement, actual
storage counts and every negative result. These short, viewed sequences measure
teacher-forced propagated error, not long-context or free-running task quality.
Capture live limits, unchanged services/dependencies, source/input identities and
completion counts; preserve evidence by content hash. No downloads, GPU access,
package installation or service handoff is needed.


## Apollo floating-point runtime qualification

`test_apollo_runtime.py` is a standalone, model-free numerical check for the
accepted Apollo GPU campaign. It requires PyTorch and exactly one visible GPU:

```sh
python3 -B -u test_apollo_runtime.py
```

Six JSONL cells compare FP32/BF16 matrix multiplication and forced BF16 math/flash
attention against CPU FP64 oracles. Prefill uses 33 causal tokens; decode uses
one query and 129 valid keys with no causal mask. This checks runtime support,
not compressed-cache quality, native four-bit arithmetic or performance.

Apollo already retains the image
`docker.io/lmsysorg/sglang@sha256:710bc11443a7b1807d69803386468101bcfced35f86bf8fe92a8209e05a2f052`
under its existing rooted Podman store. The executable is
`/mnt/microvms/ai-evaluation/runtimes/tools/podman/bin/podman`, with
`--root /mnt/microvms/ai/cache/podman-root --runroot /run/apollo-ftw-containers`.
No installation or personal dependency overlay is needed. The image contains
PyTorch 2.13.0+cu130 and nvcc 13.0.88; preserve exact identities on replay.

Use a unique owned trial directory and archive this probe plus the test contract.
Verify source hashes before and after. Expose one card at a time using the existing
CDI index names `nvidia.com/gpu=0`, `=1`, `=2`, after verifying their UUID mapping.
The current CDI file does not expose UUID aliases. Each run uses UID 1000:GID 100,
`--pull=never --network=none --read-only --cpus=4 --memory=8g --memory-swap=8g`,
`--pids-limit=256 --timeout=180 --cap-drop=ALL --security-opt=no-new-privileges`,
a 256-MiB temporary filesystem and a read-only source mount. Override the image
entrypoint with `/bin/bash -c 'nvcc --version >&2; python3 -B -u test_apollo_runtime.py'`.
Capture container configuration before start, live state, stdout/stderr, exit code,
final state and host GPU/service state. Run all three cards despite numerical
failures. Preserve all 18 cells; unavailable or timed-out cells are failures,
not skipped passes. Remove only the three owned stopped containers after saving
receipts. No model mount, network access or standing service change is needed.

## Stock Q4 comparison

`stock_q4_probe.py` compares Transformers' Quanto cache defaults with the existing
fixed linear token/channel controls. See [stock-q4-contract.md](stock-q4-contract.md)
for the four arms, frozen inputs, Qwen-then-Granite order and resource bounds.
Weights remain FP32; every arm uses native eager attention and one-token forwards.

Run `python test_stock_q4_probe.py` for CPU qualification, or append `cuda:0` for
the GPU check. These require the pinned runtime and optimum-quanto 0.2.7; Quanto's
native extension build directories must be writable while package source remains
unchanged. The runner requires a pinned model path, hashed manifest and document
index; `--help` lists arguments. It retains all per-position metrics and sampled
resident cache bytes. The controls' encoded ledger is separate from their actual
FP32 resident cache, so it does not imply a runtime memory saving.

For the split Qwen model, also run `python test_stock_q4_multigpu.py` with the two
qualified GPUs visible. The runner selects each decoder's CUDA device because
Quanto's native unpack launch does not guard it; it rejects non-default streams
and restores the caller's device after evaluation. This context applies equally
to the clean reference and every quantized arm.

The Q2–Q6 extension uses `--bits 6 5 4 3 2` on the same runner. Quanto stock arms
run only at Q2/Q4; other stock widths are explicitly unsupported. Own token and
channel controls run at every width. Run `python test_stock_qbits.py` in the
qualified runtime for scalar, packed-byte and native-model checks.
