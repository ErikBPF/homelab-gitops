# Portable compressed-KV reference

This first slice implements the integer packing and normalized segment/LUT
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
