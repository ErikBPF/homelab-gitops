# Cartesian precision follow-up contract

Status: accepted continuation from the user's “do as recommended.” This refines
that instruction; it does not replace the original all-variant campaign.
Ownership: GitOps implementation, Homelab plan/results, existing Orion/Apollo hosts.

## Scientific controls

- Keep the exact Qwen3-4B snapshot, FP32 weights/eager attention, model RoPE and
  Cartesian components. No radius normalization or log-domain attention.
- Reuse the cached public LiveCodeBench test6.jsonl snapshot and its verified hash.
  Read only platform, question_id and question_content. Deterministically rank
  unique documents by SHA256 of seed 4827 and identity, retain the first 18 with
  at least 257 tokenizer tokens, and freeze their first 257 token IDs and hashes.
  Assign six calibration, six selection and six evaluation documents by rank.
  These splits are held out from codec fitting, not claimed absent from model
  training. Do not use solutions, tests or evaluation metrics for fitting.
- Fit and select parameters using calibration only. Freeze all tables/parameters
  before scoring selection/evaluation. Selection remains a diagnostic split.
  Save every candidate calibration loss and all final variants, including losses.
- Final teacher forcing has 256 target positions per evaluation document. Report
  per-position KL, target NLL change, logit RMSE, argmax agreement and cumulative
  target loss against an independent clean FP32-cache pass of the same model.
  Average positions within documents, then documents equally. No task-score claim.

## Causal grouped cache

Use ordinary full-attention DynamicCache layers without offloading or sliding
windows. K-token and K-channel arms share group size 32 and an identical raw K tail.
V always groups 32 feature components within each token and is encoded immediately.

On each layer update, read that layer's existing length. If a nonempty old length
is divisible by 32, encode its final raw 32-token K block once before appending.
Never mutate a previously returned tensor or requantize older sealed groups.
Append current K raw. Reject a chunk crossing the next 32-token boundary.
Thus after n>0 tokens the raw K tail is 1+((n-1)%32), including a 32-token tail
at n=256; all queries within that block use raw keys. This policy is explicitly
separate from KIVI's implementation. Token and channel arms differ only in the
axis grouped when an old block is sealed. K/V/KV modes preserve unselected inputs.

The evaluator uses aligned chunks of at most 32 tokens. Test numerical equivalence
to single-token appends on a tiny Qwen3 with 66 token IDs (65 processed tokens),
including boundaries 31/32/33/63/64/65. Test future-outlier prefix invariance,
first-block K-only identity, immutable sealed groups, separate cache instances,
chunk rejection and exact payload/scale/raw-tail/table/working-buffer byte counts.
Keep the historical zero-tail token-local linear4/log4 controls as separate arms.

## Four-bit scalar grids

A shared codec uses signed fields -7..7, an explicit zero, group32 FP32 scales,
nearest physical decoded levels. Linear grids retain historical ties-to-even;
log and learned grids break ties toward zero. Test the explicit group-max-7,
component-1.5 witness (linear code 2). Preserve actual low-field-
first two's-complement packing and decode the stored bytes. Finite zero groups
round trip exactly; reject invalid shapes, nonfinite inputs/parameters and unsafe
scale arithmetic. Fixed linear and eight-octave log results must match historical
round trips on CPU, including wire bytes. CPU and GPU inputs remain on their device.

For each K grouping layout retain seven configurations: fixed linear4, calibrated
linear4, fixed eight-octave log4, calibrated log4, and three learned nonuniform4
seeds (4827/4828/4829). Also retain zero-tail token-local fixed linear4/log4.
This is 16 scalar configurations, each with K-only, V-only and combined modes.

Linear clipping candidates: 0.5, 0.75, 0.9, 1.0 times group maxabs. Log candidates:
the same clips crossed with spans 2, 4, 6, 8, 12 octaves. Select K and V separately
by mean calibration frozen-Q attention NMSE, with declared candidate order as tie
break. Never select on evaluation. Learned grids fit seven positive levels plus
fixed zero, in normalized maxabs units, using calibration samples only and 20
fixed Lloyd iterations; keep all three seeds. Limit each side/layout to 8192
deterministically sampled groups (at most 262144 normalized component values),
with retained sample indices and hashes. Charge actual parameter/table bytes
separately; common 4-bit payload/scales do not imply identical total storage.

Tests: independent physical-level and signed-byte golden oracles, boundary/tie/zero
cases, old-codec exact parity, grouping-axis fixtures, calibration-only fitting,
deterministic candidate selection and table/parameter accounting.

## Dictionary budget and attention-weighted fitting

Fit Cartesian residual VQ with four and five stages, each with 256 eight-component
FP32 centers and one uint8 index per block. Use at most 8192 deterministically
sampled calibration eight-component vectors per K/V side. Retain full-pool indices
and sample hashes, and reuse identical samples for uniform and weighted fits.
Fit separate global K/V tables for
seeds 4827/4828/4829; 12 fixed weighted Lloyd iterations per stage. Decode the sum
of the centers selected by the actual stored index bytes. No radii or padding.

Run both ordinary reconstruction and attention-weighted reconstruction objectives.
Both use Euclidean assignments. The second weights calibration vectors by the
mean diagonal sensitivity of frozen attention output across their eight components:
V sensitivity is sum over sharing query heads of p_t^2; K sensitivity is the sum
of p_t^2 * attention_scale^2 * q_d^2 * ||v_t-output||^2. Use the actual final-token
query, original keys/values and GQA grouping. Floor vector weights at 1e-4 times
their positive mean, then normalize mean to one. All-zero sensitivity uses uniform
weights and is explicitly reported. Weighted centroids minimize this local proxy;
it is not direct optimization of sequence KL or future task quality.

This yields 12 dictionary configurations, each K/V/KV. Apply the same 32-token K
tail as scalar token-layout controls. Count 4/5 payload bytes per eight values,
all 4/5-stage FP32 tables and raw tails. Four stages are below Q4's payload-plus-scale rate;
five stages add table cost above the Q4 payload rate. Report exact measured totals,
not a fictitious equal-byte comparison. Keep both rates even if one loses.

Tests: weighted centroid oracle, deterministic seeds/stages, actual uint8 payload
roundtrip and table bytes, finite guards, manual/autograd attention-sensitivity
oracle including GQA, uniform all-zero fallback, and a fixture showing that
weighted fitting differs from the uniform control without using evaluation data.

## Complete matrix and execution

There are 28 final configurations, each K/V/KV: 84 tracks per document. Run frozen-Q
comparisons on all 18 documents (1512 rows) after fitting. Run all 84 tracks on each
of six fresh evaluation documents: 504 tracks and 129024 variant-position records.
Calibration sweeps and exact-replay checks are additional receipts, not new samples.
No negative result removes another configuration, seed, mode or original model.

Observe assertion RED before implementation; commit a local scoped RED anchor.
Run focused tests, then existing CPU regression tests, independent review and a
small actual-model fit/causality qualification before the full matrix. Numeric
failures retain raw evidence and cannot count as passes. Cap fix cycles at three.

Use source-hashed offline read-only bounded containers, with existing dependencies.
Orion CPU capture/fitting: at most four CPUs and 32 GiB per job. Apollo may reuse
Orion's exact model via verified LAN artifact transfer, with FP32 weights split
across the two idle 5060 Ti cards under 12 GiB weight-placement budgets per card;
reject CPU/disk offload. Model placement requires a measured fit check first.
Each trial has a 20-minute deadline; shard the full matrix by document/configuration
without pruning, and preserve completion IDs for safe continuation. Record actual
resource limits, model/source/input hashes and before/after host health. No package
installation, external model download, standing service change or push is needed.
