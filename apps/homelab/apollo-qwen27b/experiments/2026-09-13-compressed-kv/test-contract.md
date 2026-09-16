# Portable compressed-KV reference: bounded first slice

Status: implementation scope follows the September 13 request to start work on
Orion while Apollo is busy. This is a CPU mathematical reference, not the full
experiment runner or acceptance of the proposed model-quality thresholds.

Seed: the existing `compressed-kv.feature` CKV-02 and CKV-04 scenarios, plus the
user's instruction to test every variant even after negative results. The original
human brief is not recreated here. The first slice binds only the invariants below;
the broader feature remains unautomated.

## Public seams and examples

`reference.pack(values, bits)` and `reference.unpack(payload, bits, count)`:

- Unsigned fields, widths 1 through 16; least-significant field first in a
  little-endian byte stream. This is a reference wire convention, not a claim of
  compatibility with an existing engine's KV format or signed INT4 arithmetic.
- `[1, 2, 15]` at four bits becomes `bytes.fromhex('210f')`.
- `[1, 2, 7]` at three bits becomes `bytes.fromhex('d101')`.
- Empty input becomes empty bytes; exact payload length is `ceil(count*bits/8)`.
- All integer fields round-trip exactly, including cross-byte fields and tails.
- Reject noninteger/bool or out-of-range fields, invalid widths/counts, truncated
  or overlong payloads, and nonzero unused trailing bits with `ValueError`.
- Validate payload shape before producing output. This helper does not parse
  model/table IDs, headers or untrusted files; those remain later work.

`reference.reconstruct(left, right, alpha, radius=1.0)` and
`reference.score_lut(query, left, right, alpha, radius=1.0)`:

- Endpoints and query have exactly eight finite components. Endpoints are unit
  directions within absolute tolerance 1e-6; alpha is finite in [0,1]; radius is
  finite and nonnegative. Invalid input raises `ValueError`.
- Normalize `(1-alpha)*left + alpha*right`, then multiply by radius.
- Reject interpolation norm <=1e-8, including the antipodal midpoint. This is the
  explicit reference policy; fitting must exclude those segments or use a future
  separately contracted representation.
- Unit orthogonal endpoints at alpha=0.5 reconstruct their normalized midpoint:
  first two coordinates `1/sqrt(2)` at radius one. Endpoints at alpha=0/1 are exact
  within floating arithmetic. Radius zero gives zero vector/score for valid direction.
- Direct score formula uses the two query-to-endpoint dot products and the same
  inverse norm. It agrees with dot(query, reconstructed vector), absolute/relative
  tolerance 1e-12 on bounded deterministic fixtures. Each query gets fresh dot products.
- Agreement is with the reconstructed representation, not original model keys.

## Verification and execution

`python3 -m unittest -v test_reference` is the stdlib runner. RED must be assertion
failures after imports compile. Commit the test contract, test file and empty seam
stubs as the RED anchor; implement only those seams afterward.

`python3 reference.py` will run deterministic numerical checks and print one JSON
receipt: input seed, field/segment case counts, packed byte counts, maximum score
error and explicit `synthetic_reference_only` scope. It performs no model loading,
GPU access, downloads, host changes or performance/quality claims.

Run from an immutable archive on Orion with a 120-second deadline, one CPU core and
512 MiB address-space ceiling. Record archive/source hashes, Python/host identity,
exit status and unchanged service health. That first slice leaves GPU/model work pending.

## Successor slice: real-cache scalar controls on Orion

The user's continuation authorizes bounded CPU model tests. CKV-02/03 supplies
the seed for physical byte accounting and separate K/V evidence. The existing
Qwen3-4B snapshot permits a diagnostic without downloading weights or taking the
GPU. This slice establishes scalar controls; it does not fit or select a learned
codec, bind CKV-03 end to end, or claim task-quality preservation.

`scalar_probe.scalar_roundtrip(tensor, bits)` returns reconstructed tensor, packed
uint8 tensor and FP32 scales. Bits are 2, 4 or 8. Inputs are finite FP32 or BF16 CPU
tensors with last dimension divisible by 32. Each consecutive group of 32 uses
max-absolute scale divided by `2**(bits-1)-1`, round-to-nearest-even and symmetric
clipping; a zero group uses scale one. Signed integers use two's-complement fields,
low field first in each byte. Scales remain FP32; reconstruction preserves shape
and input dtype without mutating input. Reject unsupported widths, nonfinite
values or invalid shapes with ValueError. This is a reference format, not an
engine-compatible or native integer compute format.

Review refinement: FP64 is outside this control's precision contract because its
finite range exceeds FP32 scales. Reject it explicitly rather than implying a
general floating-point codec. The model diagnostic itself uses FP32 inputs.

Runnable checks must establish known signed packed bytes, zero-group behavior,
actual payload/scale storage, bounded reconstruction error for all widths, shape
and dtype preservation, unchanged input, and invalid-input rejection. Observe
assertion failures from empty seams and commit that RED anchor before green.

The model diagnostic uses the pinned local Qwen3-4B snapshot, eager attention,
FP32 CPU weights/cache, evaluation mode and six fixed public synthetic prompts
(English, Portuguese and code), at most 64 tokens each. Prompt text, token IDs,
model revision and runtime versions are recorded. These prompts are diagnostic
fixtures, not calibration data or a benchmark. No fitting means no fit-seed claim.

For each prompt, prefill all but the last token once, then compare one identical
last-token step against the original cache with: 2/4/8-bit round trips and BF16
round trip, each K-only, V-only and combined. Use a fresh independent cache for
every step so append mutation cannot contaminate comparisons. Record per-side
NMSE, payload and scale bytes, baseline logical FP32/BF16 bytes, logit RMS error,
KL(original || perturbed) and top-token agreement for every case. No negative
result prunes another case. Count untouched cache sides in each variant's bytes.
`evaluate_prefix(model, token_ids)` is checked against an actual randomly
initialized two-layer Qwen3 fixture: all 12 cases, exact byte totals, original
cache unchanged, finite metrics and cached baseline agreeing with full-sequence
logits (maximum absolute error below 1e-4 for the tiny fixture). Record that
baseline error for the real model too. This fixture tests mechanics, not quality.

This simulates reconstructed prefix caches; appended tokens and attention remain
FP32. It measures neither packed execution nor generation quality, kernels,
transport or speed. No acceptance threshold is invented. All H1–H9/P1–P5 remain
scheduled in the host plan.

Run an immutable source archive in the existing cached container image with no
network, no GPU/device mounts, read-only root/model mounts, dropped capabilities,
four CPU threads, 32 GiB memory/no swap and a 20-minute container deadline. Use
only a unique trial directory for output. Preserve image/source identities,
exit status, numerical checks and unchanged chat/retrieval health. Do not install
packages or stop services. A runtime failure is a blocker, not a measured negative.


## Successor slice: frozen global dictionary and segment diagnostics

Basis: the user's continuation, CKV-02/03/04, and the host plan's learned-codec
comparison. The prior six prompts were already inspected; they remain replay
fixtures. `dictionary-prompts.json` freezes 18 fresh public synthetic documents
before capture, six each for calibration, selection and evaluation. Topic groups,
IDs and canonical casefolded/whitespace-normalized texts must be disjoint. These
small fixtures test mechanics and conditional distortion, not task quality or
cross-model transfer. All configurations are fixed here; selection is reported
separately and does not tune parameters or select a winning seed.

`validate_documents` accepts well-formed nonempty records with unique IDs, groups
and canonical texts, and only the three named splits; invalid input raises
ValueError. `capture_final_qkv` uses the pinned Qwen3-4B CPU eager FP32 model with
one unpadded sequence of at most 64 tokens. Capture all layers' post-RoPE K,
unrotated V and final-token post-RoPE Q. Reconstruct Q through the installed
normalization, projection and rotary mapping. The scaled, GQA-expanded QK softmax
must agree with the actual eager final attention row (atol 1e-6, rtol 1e-5), or
stop as a correctness blocker. Preserve scale, token IDs, shapes and capture hash.
The captured K/V include the final input token, unlike the previous last-token
prefix diagnostic. No logits or generated-answer claims follow from this capture.

`fit_dictionaries(vectors, seed)` takes finite CPU FP32 eight-component vectors,
at least 256 rows, and returns global FP32 unit directions: `vq` [256,8] and
`segments` [32,2,8]. Use spherical Lloyd fitting for ten iterations, deterministic
sample initialization and empty-cluster retention. VQ fits 256 centers; the segment
family fits 64 centers and greedily pairs each first remaining center with its
nearest remaining direction. This is a fixed-endpoint heuristic, not joint
segment optimization. That stronger variant remains scheduled. Fit seeds are
4827, 4828 and 4829; every seed runs. Separate K and V dictionaries pool all layers
and heads under the same consecutive eight-component convention. Sample at most
8192 calibration vectors per side, recording sample identity. No selection or
evaluation vector enters fitting. Store tables and hashes before either split is
scored; verify hashes afterward. Zero vectors use radius zero and direction e1.

`codewords(kind, table)` returns 256 normalized eight-component directions. VQ
uses its centers. Segment code `8*s+a` selects segment s and alpha a/7, a=0..7,
then normalizes the endpoint blend. Reject invalid/nonunit/zero directions or
nonfinite data. `dictionary_roundtrip` returns reconstructed FP32 vectors, actual
uint8 codes and BF16 radii. Decode from these stored fields, including BF16 radius
rounding; never grade the unrounded encoder approximation. Assign in bounded
chunks; fitting/decoding must not materialize all capture-by-code distances.
Known orthogonal segment code 3 decodes direction [0.8,0.6,0,...].

Each compressed eight-component block uses three payload bytes (one code and two
radius bytes). VQ tables cost 8192 bytes per compressed side, segment tables 2048.
Report raw totals and an explicitly padded common table allocation of 8192 bytes
per compressed side. Segment padding is 6144; the existing scalar-2 control uses
8192 padding, since its group32 FP32-scale format also costs three bytes per eight
values. Report scalar-4 separately as a higher-rate control. Padding is deliberate
comparison overhead, not compression efficiency. Count untouched FP32 cache sides
for K-only/V-only cases and charge global tables once per independent document.
Report the 8192-byte expanded direction table as decode working storage, separate
from stored endpoints. Exclude packet metadata and allocator overhead explicitly.

For selection and evaluation documents, grade every learned seed/family and
scalar-2/scalar-4 controls, each K-only, V-only and combined. Report per-side NMSE,
raw/matched bytes, and conditional attention-output NMSE using original frozen Q,
the model's scaling and GQA mapping. This does not propagate codec error through
model layers or append generated tokens. Expected ledger: 216 learned rows plus
72 deterministic scalar rows = 288 comparisons. Scalars are not refitted or
repeated as independent seed evidence. Negative outcomes never remove another
case. Zero-energy NMSE is zero only when squared error is zero; otherwise invalid.

The tiny Qwen3 oracle, split-leak rejection, seeded fit, normalized interpolation,
actual signed/unsigned wire fields and malformed-input checks must fail assertions
from empty seams before implementation. Preserve the local RED commit. Reuse the
cached offline image/dependency mounts and existing scalar helper; install nothing.
Run all 18 captures, fits and comparisons in one owned four-thread/32-GiB container
with no swap, network or GPU devices, a 20-minute deadline and a 1-GiB artifact
budget. Preserve source/model/dependency/input hashes, all raw rows, exit status,
and unchanged service health. A timeout is a blocker, not a scientific negative.


## Successor slice: joint endpoints on the retained captures

Basis: the user's continuation and the explicitly pending joint-segment variant.
Use only the retained dictionary bundle
`73465d6f361dff338674c1a9d9a293778918f846fabdcfc1bf0d8cbb3cbdc889.tar`.
Its evaluation results have already been inspected. This is a paired replay
diagnostic, not a fresh held-out confirmation or a full H2 verdict.

`refine_segments(vectors, initial)` takes the same finite FP32 calibration rows
and a valid [32,2,8] FP32 unit-endpoint initializer. Clone inputs; optimize both
endpoints with 50 fixed Adam steps at learning rate 0.03, recomputing nearest
segment/alpha assignments each step. Optimize mean squared unit-direction error,
not attention error or radius-weighted reconstruction. Normalize blends using the
existing eight alpha levels, differentiate selected directions, and project
endpoints to unit length after each step. Reject nonfinite/degenerate states.
Return final detached endpoints and all 51 calibration losses (initial plus each
step). No early stopping, best-checkpoint selection or loss-based reversion.
The final iterate remains a measured candidate even if it loses. Preserve initial
and final objectives, optimizer settings, every seed and input/table hashes.

Reuse all three original seed initializers and their exact stored 8192-row K/V
calibration samples; fit K and V separately. No viewed selection/evaluation data
enters gradients or parameter selection. Freeze all six refined tables before
scoring. Representation, BF16-radius rounding, byte accounting, frozen-Q attention
and K-only/V-only/combined modes remain unchanged. The expanded direction-table
count is not a peak-memory measurement.

Replay all 288 existing baseline comparisons and verify them against the source
receipt. `verify_replay` requires unique matching document/family/seed/mode IDs,
exact counts and nonnumeric/byte fields, and finite metric agreement within
atol 1e-8 plus rtol 1e-6. A mismatch is a correctness blocker. Then score 108 new
joint-segment cases: 12 documents x three seeds x three modes. Retain all 396 rows
and every loss. Compare each refined seed with its own paired initializer;
selection and evaluation summaries remain separate.

Tests must observe assertion RED for refinement on a simple off-direction fixture,
final-objective consistency, input immutability, invalid input rejection, and
replay refusal of changed metrics, identities, counts and byte totals. Commit the
RED anchor before implementation. Reuse existing codecs and comparison functions;
do not reload model weights or recapture prompts. Verify all source input hashes
before and after; save refined tables and provenance in a new owned directory.

Run model-free on Orion using the same cached image and read-only dependencies,
with four CPU threads, 4 GiB memory/no swap, no network/GPU devices, a 20-minute
deadline and a 64-MiB new-output budget. Mount the verified retained artifact
read-only; no model mount is needed. Record service health and preserve the new
artifact by hash. Other segment objectives, specialization, table-size sweeps,
models and GPU paths remain scheduled regardless of this result.

## Corrective slice: Cartesian validation before logarithmic encoding

Human correction, September 13: "lets redo validations so far in a cartesian
space. I believe it is the default on kv caches. Our experiemnt is not isolating
logn representations for a change in coordinates spaces". Preserve this correction
without replacing the missing original brief. This run establishes ordinary linear
Cartesian codecs. No logarithmic codec has been implemented or validated; a future
signed-log encoding must hold the Cartesian axes and other controls fixed.

Grounded challenge: the previous `fit_dictionaries` and `refine_segments` call
`_directions`, and decode multiplies normalized codewords by a BF16 radius. Both
learned families therefore changed the representation before a Cartesian learned
baseline existed. Original Qwen3 captures already contain real-valued Cartesian
components (post-RoPE K and Q, unrotated V); reuse them exactly. Scalar controls
already operate on Cartesian components and must be rerun unchanged. Do not
undo RoPE: it is part of the model, not this codec's coordinate transformation.

`cartesian_probe.codewords(kind, table)` accepts finite CPU FP32 VQ [256,8] or
segment [32,2,8] tables. VQ words are their centers; segment code `8*s+a` means
`(1-a/7)*left + (a/7)*right`. Never normalize, extract radii or take logarithms.
Zero, nonunit and antipodal endpoints are valid, including a zero midpoint.
`roundtrip(vectors, kind, table)` chooses minimum squared Euclidean distance in
bounded chunks and returns reconstruction from actual uint8 codes plus those
codes, preserving finite CPU FP32 [N,8] input and its shape. Lower index wins ties.
Reject invalid kind, shape, dtype, device or nonfinite input with ValueError.

`fit_dictionaries(vectors, seed)` requires at least 256 rows. Initialize raw-vector
centers with the same seeded permutation convention as the earlier probe. Run ten
Euclidean Lloyd iterations for 256 and 64 centers, retaining empty centers. Pair
the first remaining 64-center index with its nearest remaining Euclidean neighbor
to form 32 fixed affine segments. Preserve magnitudes and signs throughout.
`refine_segments(vectors, initial)` clones valid raw endpoints and uses 50 fixed
Adam steps at 0.03 on original-vector MSE, recomputing discrete nearest codeword
assignments each step. No endpoint projection or normalization. Return detached
final endpoints and 51 losses; no pruning, best-checkpoint choice or early stop.

`score_lut(query, segments)` returns the 256 affine scores from 64 endpoint dot
products and linear interpolation. It must agree with direct reconstructed-vector
dot products (atol/rtol 1e-5 on bounded FP32 fixtures). This is a CPU correctness
oracle, not a fast implementation claim. The existing packing tests remain valid.

Tests first: same-ray different magnitudes must choose different Euclidean codes;
explicit negative/nonunit and zero-segment examples must reconstruct without a
radius; uint8 storage and unchanged inputs must be checked. Seeded fitting must
retain a known nonunit constant center. Joint fitting must reduce the off-line
fixture MSE, preserve input, return all 51 losses and match its final objective.
Check LUT/direct parity for every code and invalid inputs. A tiny captured Qwen3
comparison must agree with independent manual reconstructed attention and charge
actual code/table/untouched bytes. Observe assertion RED from importable stubs,
commit the RED anchor, then implement and independently review before running.

Replay the exact retained dictionary and joint bundles by verified SHA256. Verify
all declared captures/samples/tables before use, load NPZ with allow_pickle=False,
and verify unchanged hashes after scoring. Use the exact original 8192-row K/V
calibration samples and all seeds 4827/4828/4829; fit separate K/V tables. Freeze
all new tables before scoring selection or evaluation. Reproduce all 396 previous
rows with `verify_replay`; these remain spherical/scalar historical controls.
Then run 324 Cartesian comparisons: 12 documents x three seeds x VQ/fixed-segment/
joint-segment x K-only/V-only/KV. Add scalar-8 and BF16 controls on all 12 documents
and modes (72 rows); scalar-2/4 are already among the 396 replay rows. Total792.
Separately rerun the unchanged original 72-case scalar model diagnostic, preserving
logit metrics and comparing against its original receipt. No failed scientific
variant removes another. Errors, nonfinite states or resource exhaustion are
explicit execution blockers, not negative scientific evidence.

Cartesian learned codes use one byte per eight values, with no radius or per-block
scale. Raw table bytes are 8192 for VQ and 2048 for either segment family per side;
also report common 8192-byte table allocation and its padding. These three learned
families have identical payload rates and equal padded totals. They are a lower
rate than the spherical three-byte blocks and group32 scalar-2 controls: do not
label that cross-family comparison equal-rate or attribute its difference solely
to coordinates. Report payload, scale, table, untouched FP32 and expanded decode
working bytes separately; padding is not useful encoded information. New scalar-8
and BF16 controls have no artificial table padding; historical scalar-2/4 replay
retains its recorded padding. BF16 rounds Cartesian cache components. Metrics remain per-side
and frozen-original-Q attention-output NMSE; no model-quality/speed claim follows.

Use one model-free four-CPU/4-GiB/no-swap offline Orion container for the 792-row
replay, and a separate four-CPU/32-GiB/no-swap container for the original scalar
model diagnostic. Both have 20-minute deadlines, read-only source/dependency/input
mounts, no GPU devices, no downloads or service changes. New table output budget
64MiB; scalar captures retain the existing128MiB budget. Inspect live limits,
record source/dependency identities and before/after health, then preserve all
receipts and fit histories in content-addressed bundles. Reuse existing helpers;
do not alter the historical codec or its archived baselines.

## Successor slice: matched-byte logarithmic grids and propagated error

Basis: the user's "lets continue" after recommending a matched-byte Cartesian
linear/log comparison followed by teacher-forced accumulated-error evaluation.
Keep the original Cartesian axes and model RoPE. This slice tests one explicit
logarithmic storage grid, not log-domain attention arithmetic or a universal claim
about logarithmic representations. Every named configuration runs despite losses.

`log_probe.log_roundtrip(tensor, bits)` accepts finite CPU FP32 tensors with last
dimension divisible by 32, bits 2/4/8, and returns reconstructed FP32, packed uint8
and FP32 scales in the existing scalar format. Let m=2**(bits-1)-1 and use the same
group32 scale s=max(abs(x))/m as `scalar_roundtrip` (zero groups use s=1). The signed
integer field z ranges -m..m, with zero decoding exactly zero. For j=abs(z)>0 and
m>1, decode sign(z)*s*m*2**(-8*(m-j)/(m-1)). For m=1 use sign(z)*s. The fixed eight
octave span is an experimental setting chosen before results, not a fitted optimum.
Choose the closest decoded level in original component space; ties go toward zero.
Preserve two's-complement fields, low field first, and reconstruct from actual
packed fields and stored scales. No norm, radius, rotation or logarithmic attention
calculation. Reject unsupported inputs and nonfinite/zero scales or arithmetic.
Log-2 is exactly linear-2 by construction; execute it and report the identity.
Actual payload plus scales is 3/5/9 bytes per eight values for 2/4/8 bits, for both
linear and log grids. No artificial table padding in these scalar comparisons.

The public `comparison(captures, bits, mode, logarithmic=False)` accepts scalar
bits 2/4/8 or the explicit BF16 control, and K/V/KV modes. Reuse original frozen-Q
attention and NMSE definitions; charge payload, scales and untouched FP32 values.
Tests establish known signed packed bytes, zero and nearest-level boundaries,
bit2 exact linear parity, all widths' identical scale/payload accounting, no input
mutation, finite arithmetic rejection, and manual reconstructed-attention parity.

The model-free runner uses verified retained dictionary and Cartesian bundles
73465d6f361dff338674c1a9d9a293778918f846fabdcfc1bf0d8cbb3cbdc889 and
a6966f9bc5951acbbb8aaea7ab6bc237e7651b59734c12b9a9f290a6f57d7e04.
Verify all 23 dictionary and six Cartesian NPZ identities and source provenance;
load arrays with allow_pickle=False. Preserve all input hashes before/after.
Run 252 rows: 12 documents x three modes x (linear2/4/8, log2/4/8, BF16).
Verify all 144 linear/BF16 rows against retained Cartesian-run metrics and actual
raw byte fields exactly; old artificial table-padding fields do not enter this
new paired scalar experiment. Verify every log2 row equals its linear2 pair.
No refitting, recapture or replay of unrelated learned-codec rows is needed here.

`teacher_forced_probe.QuantizedCache(config, codec, mode)` subclasses the installed
DynamicCache. Its update intercepts only incoming K/V, applies codec(tensor,side)
to selected sides, then calls the parent update. The model consumes decoded K/V
before attention, during prefill and every append. Never requantize stored entries,
reset to reference K/V, share cache storage across variants, or quantize Q. This
captures each variant's own propagated layer states, unlike the old perturb-once
FP32-prefix diagnostic. Existing raw FP32 and BF16 controls remain distinct.

`evaluate_teacher_forced(model, token_ids, codec, mode)` runs a fresh quantized
cache over token_ids[:,:-1] and returns next-token logits and its final cache.
A full causal pass is sufficient because all codecs group only within a token's
last dimension, never across tokens. A tiny Qwen3 test must prove logits and final
caches agree with separate prefill-plus-append execution, and that old entries are
unchanged after append. Test independent caches, K-only/V-only isolation and input
nonmutation; include nonlinear scalar and learned codecs. A deliberately simple
codec must show old entries are not quantized twice. The clean cached baseline
must agree with a no-cache causal forward within atol/rtol1e-4 on tiny fixtures.

`logit_metrics(reference, actual, targets)` returns per-position finite
KL(reference||actual), logit RMSE, reference and variant target NLL, NLL increase
and top-token agreement. Targets are the identical next tokens for every variant.
Test zero-error identity and an independent small-logit analytical example, correct
alignment/counts, and malformed/nonfinite input rejection. These are teacher-forced
propagation measurements, not free-running generated-answer quality.

Score all 189 next-token positions in the six retained evaluation documents.
Run all nine Cartesian learned family/seed configurations, linear2/4/8, log2/4/8
and BF16, each K/V/KV: 288 tracks and 9072 variant-position observations, plus
189 clean FP32 reference positions. Reuse frozen Cartesian tables, do not fit on
these viewed documents, and retain every position and loss. Record both per-track
and per-position results; average documents before an overall summary. Capture
encoded payload/scale/table and untouched FP32 bytes separately from reconstructed
working memory. Q4/log4 comparisons have identical bytes; learned-vs-Q4 comparisons
retain their distinct rates. Historical spherical codecs remain separately
scheduled; this continuation preserves the user's Cartesian correction.

Run immutable archives on Orion with four CPUs, no GPU/network, read-only source,
inputs/dependencies/model, no swap and 20-minute deadlines. The frozen-Q run uses
4GiB; teacher forcing uses32GiB and cached Qwen3-4B FP32 eager weights. Shard teacher
forcing by document if needed, recording exact completed tracks; no timeout counts
as a scientific loss or permission to prune another track. No package install,
download or service change. Preserve source/bundle/dependency hashes, observed
limits, test RED/GREEN logs and unchanged service health. A fixed-model causal pass
avoids unnecessary repeated decode forwards; correctness is gated by append-parity
tests, not an assumed equivalence. Longer fresh-data and generation tests remain
later work, regardless of the result.


## Apollo cached GPU runtime qualification

This is a test-only prerequisite to the accepted GPU campaign, not a new codec.
Use the cached SGLang image digest
`sha256:710bc11443a7b1807d69803386468101bcfced35f86bf8fe92a8209e05a2f052`
and expose one verified physical GPU per container. Run every case on the RTX 3070
and both RTX 5060 Ti cards, regardless of earlier failures: 18 cells total.

- Two matrix products: FP32 with TF32 disabled, and BF16; compare against CPU FP64
  products of inputs rounded to the tested dtype.
- Four BF16 attention cases: forced MATH and forced FLASH_ATTENTION, each with
  causal prefill Q=K=33 and noncausal decode Q=1, K=129, batch 1, four heads,
  head dimension 128. Decode keys are all valid past/current keys. The independent
  CPU FP64 oracle explicitly scales, masks and applies softmax.
- Require finite outputs and fixed maximum absolute/relative L2 bounds: FP32
  matmul 1e-4/1e-5, BF16 matmul 0.1/0.01, and BF16 attention 0.02/0.01.
  Synchronize within each cell. Preserve raw exceptions; unsupported or
  failed cells make aggregate status nonzero, and cannot count as passes.
- Require exactly one visible GPU. Record device identity, compute capability,
  image/source hashes, PyTorch/CUDA versions, compiler version and container limits.
- Offline, read-only, UID 1000:GID 100, four CPUs, 8 GiB memory/no swap, 180 seconds
  per GPU. Preserve source and receipts; verify no remaining experiment container
  or GPU process and unchanged standing service start times afterward.

No model is loaded. These checks establish floating-point runtime readiness only;
INT4/NVFP4 kernels, GQA, model parity and throughput remain separate campaign cells.
