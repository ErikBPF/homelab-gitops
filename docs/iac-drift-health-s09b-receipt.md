# S09B drift lifecycle health: source receipt

**Status:** independently reviewed source; owner gates passed twice. Producer
publication/runtime handoff still blocks merge. **Date:** 2026-11-21.

## Outcome and reason

Four Grafana rules expose a scheduled drift check that never runs, fails before
its script, exceeds its deadline, stops completing, or cannot establish required
notification delivery. Discovery is the single independently expected producer.
A completed drift finding remains healthy execution; IaC's existing notifier
continues to own that finding. No automatic apply, retry service, extra scheduler
or notification integration was added.

Worktree `worktrees/iac-drift-health-s09b`, branch `feat/iac-drift-health-s09b`,
base `05ff0c7640c29a0d05222bc376447d50a580de2b`. The canonical checkout's unrelated
local work was preserved. Source changes are rules, existing renderer/PromQL tests,
[operator guidance](alerting.md#iac-drift-health), and this receipt.

## Contract and decisions

The S09 owner froze seven `homelab_iac_drift_*` lifecycle gauges with fixed
`instance="discovery"`, outcome 0–6 and notification 0–4 semantics documented in
the runbook. Complete clean/drift advances completion even if notification fails;
invalid result metadata does not. The producer bounds its begin reporter to 30 seconds and main command to 3,600
seconds, including the existing 900-second shared lock wait. Five possible
30-second cleanup/reporting phases yield the conservative 3,780-second consumer
threshold. The five-minute pending period applies after that predicate.

The coordinator accepted a provisional 26-hour completion threshold: daily
cadence plus 15-minute jitter, 63-minute conservative lifecycle budget and 42-minute ingestion
margin. Finished execution failures and overdue running attempts take precedence
over stale completion; completed-but-undelivered finding notifications have their
own health class. A healthy Discovery host scrape gates these workload alerts,
leaving whole-host outages and datasource failures to existing owners.

The generic systemd failure rule is deliberately unchanged. A prior healthy
lifecycle sample can survive failure of the publisher itself. Suppressing that
fallback on mere metric presence would hide a new failed unit until the stale
threshold. A generic unit warning may coexist with the specific execution warning;
neither adds a second drift-finding notification.

## RED, GREEN and limits

After the unchanged baseline passed, the expanded PromQL contract failed with
`missing IaC lifecycle health rule: homelab-iac-drift-execution-health`. The four
rules then passed actual promtool evaluation, preserving Discovery identity.
Fixtures cover healthy clean/drift, every finished failure class, delivery states,
3,780-second/26-hour boundaries, never-run and each missing gauge, a missing job
beside a healthy peer, whole-host loss, failed scrape, timeout/recovery transitions,
and old healthy lifecycle gauges beside a currently failed systemd unit.

One added shell assertion initially used an unsupported `yq` function. That was a
test-harness failure, not behavioral RED; selecting the named group corrected it
without changing acceptance. Final owner gate passed:

```bash
source /tmp/homelab-gitops-host-integrity-tools/env.sh
bash tests/monitoring-contract.sh && python3 tests/monitoring-promql.py && env -u GOROOT go test tests/grafana-alert-template_test.go && bash tests/grafana-alert-status.sh && echo S09B_DRIFT_HEALTH_OK
```

This uses existing CI-pinned promtool 3.14.0/PyYAML 6.0.3 and exact ignored chart
dependencies. All four commands passed. No live drift run, infrastructure change,
notification injection, rule deployment or recipient receipt occurred for S09B.
The full repository suite was not run. Static policies and expression evaluation
do not prove Grafana pending/routing/delivery behavior.

## Handoff and recovery

The monitoring application auto-syncs main. Hold merge until the producer is
published, deployed and its compatible gauges/budget are observed. Root independently reviewed this draft against the producer contract, found no
material issue, and reran all four owner checks successfully. First-draft evidence
is preserved at `/tmp/iac-drift-health-s09b-first-draft.patch`. Later
live proof needs named authority, exact revisions, a bounded health-class scenario,
fresh producer recovery and actual firing/resolved operator receipt. Preserve the
existing finding notifier and shared apply lock. Withdraw deployed rules through
reviewed main-branch source changes with explicit UID tombstones; never roll back
infrastructure simply to make monitoring green.

## Reviewed phase-budget correction

Pinned systemd source review showed independent phase timeouts; the earlier
3,630-second whole-service claim was incorrect. Boundary tests at 3,780 and 3,781
seconds failed against the prior rules (`/tmp/s09b-3780-red.log`); the matching
consumer threshold and documentation were corrected. Producer begin/finish are
now separately bounded and stale results without a matching begin are rejected.
