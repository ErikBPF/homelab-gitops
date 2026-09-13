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
