# Stock Q4 comparison contract

Human seed: “lets test default q4 on qwen to validate if we actually created any benefit. Then repeat tests on granite”.

Use Qwen3-4B first, then Granite 3.3 2B Instruct. Preserve FP32 weights, native
eager attention and RoPE, and tokenwise clean/variant execution. This tests KV
quantization, not quantized model weights. No speed claim is part of this slice.

“Default Q4” means the installed Transformers 5.12.1 `QuantizedCache` with its
documented default backend Quanto: four bits, key/value axes zero, group size 64,
residual length 128. Pin optimum-quanto 0.2.7 as an isolated dependency overlay;
never replace installed Torch/Transformers or alter upstream quantization code.
DynamicCache itself is unquantized and remains the clean reference.

Run four combined-K/V arms on all six previously frozen evaluation documents:
stock Quanto defaults; stock Quanto with group32/residual32 (explicit diagnostic,
not relabeled default); our fixed linear token-group32/raw-K-tail32; our fixed
linear channel-group32/raw-K-tail32. Keep negative results and distinguish the
different residual schedules and quantizers. No fitting or calibration is needed.
Qwen uses the original 257 token IDs. For Granite, decode the Qwen document text,
retokenize with Granite without added special tokens and freeze at most 257 IDs.
Record each model's actual position count; never compare cross-model raw KL means
as if their references or tokenizations were identical.

For every document, run an independent clean cache and every arm. Retain target
IDs and per-position KL(reference||variant), logit RMSE, target NLL increase,
top-token agreement and cumulative target NLL differences. Aggregate positions
within documents, then documents equally. The prior Qwen arm results serve as
replay checks; a mismatch must be explained before a new benefit claim.

Record actual stock-cache tensor storage recursively through Quanto's tensor
wrappers, deduplicating backing storage. Distinguish those resident bytes from
our codec's hypothetical encoded-byte ledger and its actual FP32 resident cache.
Preserve each variant's end-of-document bytes and maximum sampled post-forward cache-resident tensor bytes.
Do not describe Python tensor storage as total process/GPU memory.

RED/GREEN checks: stock defaults and upstream class identity; packed-tensor bytes
including scale/shift and aliased storage; invalid variant rejection; tiny Qwen
and Granite clean-versus-clean tokenwise parity; finite outputs/position alignment
for all arms; candidate ledger versus resident storage. No custom Q/K capture
hooks or replacement attention math are required: call each model's own forward.

Apollo is the authorized target. Reuse its pinned SGLang image, unchanged services,
offline/read-only inputs and resource receipts. GPU jobs: four CPUs, 32 GiB,
no swap, 20 minutes per document; six Qwen jobs then six Granite jobs, at most
four hours of aggregate GPU-job wall time (six physical GPU-hours maximum). Small qualification jobs have a separate aggregate
20-minute cap. A job failure stops dependent runs; preserve outputs and allow
at most three repairs per failure. Download the pinned public Granite checkpoint
into an experiment-owned artifact, verify hashes, then mount read-only. No push,
deployment or standing-service change is part of this experiment.

Runtime correction: the initial asynchronous and synchronized full Qwen trials
both failed in Quanto's native CUDA unpack call; retain both receipts. A two-GPU
regression then asserted that native decoder execution had the wrong active CUDA
device. The runner selects each decoder layer's parameter device before its
forward, for clean and all variant arms. It requires default CUDA streams,
removes only its own hooks and restores the caller's device in a finally block.
This changes execution context, not upstream source, attention or quantizer
settings. The corrected campaign uses ordinary asynchronous execution and still
requires prior own-arm replay. No speed claim follows. The queue checks the
original four-hour wall deadline before reserving each 20-minute document slot.

## Q2–Q6 extension

Human correction: “check over q6-q2”. The same frozen Qwen and Granite inputs now
cover own token/channel controls at 2, 3, 4, 5 and 6 bits, with stock Quanto arms
only at 2 and 4 bits. Record other stock widths as unsupported. The own symmetric
grid has `2^bits - 1` levels, including zero; Q2 has three. Q4 keeps the original
codec exactly. Other widths pack byte bit planes and reconstruct from those bytes.
Scales stay FP32 and group/tail policies remain unchanged. Actual own cache
residency remains FP32, separately reported from the encoded ledger.

Use `--bits 6 5 4 3 2` for 14 tracks per document, 168 tracks total. Default CLI
behavior remains Q4. `test_stock_qbits.py` covers packing, reconstruction, byte
counts, invalid inputs, direct upstream rejection and native model execution.
Replay all 48 previous Q4 tracks. Extend the per-document bound to 30 minutes and
the fixed campaign ceiling to six hours; qualification retains a 20-minute cap.
Keep all negative results. No serving-engine substitution or weight quantization
is part of this extension.
