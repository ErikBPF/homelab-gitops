# Temporary KEDA metrics placement after w-1 exit 139

**Status:** Source workaround prepared; merge and deployment held by the incident
coordinator. This does not repair w-1 or prove the cluster substrate healthy.

## Outcome and reason

The user's instruction was “do fixes”. The deployed KEDA metrics apiserver on
w-1 repeatedly exited 139 immediately with no application output. The operator
and admission webhook on that node remained running. Its deployed image and
arguments matched GitOps revision `05ff0c7640c29a0d05222bc376447d50a580de2b`;
there was no demonstrated chart/image mismatch.

Two authorized disposable probes isolated the failure from production arguments,
certificates and Kubernetes API access. Both ran `/keda-adapter --help` using the
exact deployed image, KEDA 2.21.1, with digest
`sha256:747e376b3e80dc4f72bf4f7043f47cc4cdb56f34b1aed9531789f474996e1c84`.
The [pinned upstream adapter](https://github.com/kedacore/keda/blob/v2.21.1/cmd/adapter/main.go)
parses help before starting its API/services.

| Node | Observed time, UTC | Result |
| --- | --- | --- |
| w-1 | 2026-11-22 00:49:53 | Exit 139 immediately; zero log bytes |
| w-2 | 2026-11-22 00:49:54 | Exit 0; 28,011 bytes of valid help output |

This supports a node-local image, snapshot, runtime or substrate fault. It does
not identify which layer is defective or establish offline filesystem corruption.
A checksum failure while reading w-1's live filesystem through debugfs is not an
offline filesystem check. SSH host-key verification was never bypassed.

## What changes and how it helps

Only the metrics server receives the chart-supported hostname selector for w-2,
the node that executed the exact image successfully. The operator, webhook,
image digests, arguments, secrets and resource budgets remain unchanged. The
production request is **10m CPU and 64Mi memory**; it is not reduced to force
placement. At the recorded preflight, w-2 requested 3955m of 4000m CPU and
11684Mi of approximately 15988Mi memory, leaving room for that pod. Capacity must
be checked again immediately before merge.

This is temporary containment: w-2 downtime can leave the metrics pod Pending
until an intentional repin or removal. It does not improve general availability
or repair the separate control-plane/storage incident. The KEDA Application
tracks `main`, and `clusters/kepler/apps/kustomization.yaml` enables automated
sync with prune/self-heal. Merging this PR therefore triggers rollout through
Argo reconciliation; a separate manual `just sync` is not a deployment gate.
Complete the control-plane recovery and recheck w-2 capacity before merge.
Keep the PR unmerged until those gates pass; automated sync has not been suspended.
After the approved merge and automatic rollout, verify placement, restart count, logs and the external
metrics API; a passing help probe alone is not production service proof.

## Evidence and limits

The rendered placement assertion failed before the selector change and passed
afterward. The same CI-wired `tests/demo-keda-ownership.sh` contract compares all
three Deployment renders with the sole hostname selector removed and asserts the
unchanged production requests. Helm needs a null override to remove the map;
the first empty-map fixture merged the old value back and was corrected, not
counted as another behavioral RED. The focused contract, rendered schema check
and whitespace check passed. Independent review reproduced the focused contract
and found no material issue. Full repository validation remains a hosted CI gate.

Both diagnostic Jobs were non-root, read-only-root, capability-free, token-free,
without volumes or secret environment; each had a 60-second deadline, zero
retries and a 120-second cleanup TTL. Initial 100m CPU requests were rejected
before execution on both nodes. One explicitly authorized infrastructure retry
used 1m requests with the same 500m/512Mi limits and 64Mi memory request after
capacity verification. Exactly two probe containers executed. No production pod
was deleted or cache repulled. Session manifests and results remain under
`/tmp/keda-exit139-*`; their observations are recorded above for review.

## Removal and recovery

Remove the temporary selector only after w-1 is repaired and an authorized exact
image probe succeeds there, followed by a controlled rollout. Removing it earlier
allows the original failure to recur. If w-2 becomes unavailable first, select a
separately verified node with capacity through source review; do not silently
weaken resource requests or SSH trust. Preparing this unmerged branch performed
no production rollout. Merging it, removing its selector, or changing placement
on `main` triggers automatic reconciliation and must pass the same pre-merge
recovery and capacity checks.
