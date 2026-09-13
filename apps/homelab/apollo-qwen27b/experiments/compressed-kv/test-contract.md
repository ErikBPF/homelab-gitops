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
exit status and unchanged service health. Keep all GPU/model experiments pending.
