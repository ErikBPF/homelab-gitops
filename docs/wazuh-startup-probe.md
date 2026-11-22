# Wazuh indexer startup allowance

## Outcome and why

Prepare a bounded startup probe so slow cold initialization is not repeatedly
terminated by steady-state liveness. Read-only evidence on 2026-11-21 showed
`wazuh-indexer-0` exit 143 after about 89 seconds, with kubelet events explicitly
reporting failed TCP 9200 liveness and restart. The source had a 60-second initial
liveness delay and no startup probe. Bounded logs did not establish the underlying
initialization stall or prove data corruption.

## Change and verification

The indexer gets a TCP `indexer-rest` startup probe every ten seconds, with a
one-second timeout and 60 failures allowed: a nominal ten-minute startup budget.
Kubernetes suppresses readiness and liveness until startup succeeds; their
existing configuration remains unchanged. Exhausting the startup budget still
restarts the container. No probe is disabled indefinitely.

The existing rendered Wazuh contract now asserts the startup bound and unchanged
readiness/liveness. Its assertion failed against the old manifest and passed with
the change; `/tmp/wazuh-startup-probe-red.log` preserves that RED. An initial test
expression used unsupported YAML map comparison; the final assertion uses jq
against rendered JSON, and the final RED was rerun before GREEN. Kubeconform found
18 valid resources, zero invalid/errors and nine skipped custom resources.
Independent review repeated the owner contract and found no material issue.
These are source/render checks, not proof of live startup or index integrity.

## Rollout and recovery

Merge and deployment remain held while the coordinator investigates divergent
etcd state and host/storage health. Argo automatically applies merged source, so
merging this PR can roll the indexer; publication is not deployment permission.
After explicit readiness approval, observe startup completion, stable restart
count and indexer health. If startup still exceeds the budget, diagnose the stall
rather than extending it indefinitely. Rollback reverts this source change; it
does not restore lost data or resolve host faults. KEDA and other workloads are
outside this change.
