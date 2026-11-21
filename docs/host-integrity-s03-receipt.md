# S03 source implementation receipt — 2026-11-20

## Outcome and boundary

GitOps now defines five identity-aware integrity alert rules and an independent,
empty-by-default expectation list. This source slice makes disappearing checks
observable without confusing a partial scan with complete coverage. No hosts are
enrolled, no cluster was contacted, and no finding or notification was injected.
Producer compatibility and publication remain prerequisites for landing; S04 owns
enrollment, deployment, and live delivery/recovery proof.

Checkout: `worktrees/host-integrity-s03`, branch `feat/host-integrity-s03`.
Initial implementation base: `6063a178b3c353ae0ecb442395f5f5747daa2752`.
Canonical branch divergence was preserved during implementation; subsequent
authorized PR preparation is recorded below. No global package updates occurred.

## Delivered behavior

Stable UIDs: `host-integrity-finding`, `host-integrity-incomplete`,
`host-integrity-stale`, `host-integrity-signature-age`, and
`host-integrity-missing-check`. All preserve `instance`/`check` identity and use
existing Grafana datasource error ownership. Findings remain independent of
partial/failed state. The chart validates expectation identities and duplicate
pairs, then creates `host_integrity_expected` recording rules selected by the
existing Prometheus operator. The production list stays empty until S04.

The [runbook](alerting.md#host-integrity) records 48-hour/eight-day boundaries,
current-scrape gating, host-loss ownership, retained-finding semantics, and the
limits of Grafana resolution and trusted remote-write input. Existing CI already
runs all four focused tests, so no workflow change was needed.

## RED/GREEN evidence

Baseline renderer and PromQL tests passed. Initial RED asserted
`missing integrity rule: host-integrity-finding`; expectation-render RED asserted
`production enrollment must remain empty` because the new configuration was
absent. A later negative fixture exposed missing signature-database evidence:
expected Orion/signatures missing-check, got no series. The rule now includes
that absence independently of general gauges, and the fixture passes.

A first implementation exposed an overly broad unknown-database selector with
an adversarial store-labelled gauge. Restricting that branch to `signatures`
removed the false positive; the test remains. These are observed failures, not
backdated RED claims. Go initially failed `go: no such tool "vet"` because inherited
`GOROOT` omitted the installed `/share/go` directory. Unsetting that environment
variable selected the existing complete toolchain; no test was disabled.

Final focused command passed with `S03_ALERTS_OK`:

```bash
source /tmp/homelab-gitops-host-integrity-tools/env.sh
bash tests/monitoring-contract.sh && python3 tests/monitoring-promql.py && env -u GOROOT go test tests/grafana-alert-template_test.go && bash tests/grafana-alert-status.sh && echo S03_ALERTS_OK
```

The temporary environment contains CI-pinned promtool 3.14.0 (official artifact,
SHA256 `f665c6da19eb7ba399c915d30c7d9793c9b417bf8a749b504bc470678631478d`),
PyYAML 6.0.3, and isolated Helm repository/cache directories. Exact chart
dependencies were fetched into ignored chart directories. These local tools are
not delivery dependencies; CI supplies its existing pinned tools.

## Remaining review and recovery

Root independently reviewed the frozen first draft and found no material source
defect. The first-draft patch is preserved at
`/tmp/host-integrity-s03-first-draft.patch`, including new files. This receipt
records reviewed source behavior, not deployed acceptance. Root independently reran all four focused gate commands; all passed with exit
status zero. Tests evaluate real rendered recording rules, loader selectors,
empty/invalid enrollment, label identities, stale boundaries, missing-series and
failure/recovery transitions. They do not execute Grafana pending durations,
Discord routing, or deployed recovery. The repository-wide validation suite was
not run; the focused S03 gate passed. Revert this slice before deployment if
necessary. After deployment, retiring provisioned UIDs requires Grafana deletion
tombstones and an authorized live absence check; merely removing YAML is not
sufficient.

## PR preparation — 2026-11-20

The user authorized publishing this work for review. The seven S03 files were
committed together, then only that task commit was rebased onto fetched
`origin/main` at `84cf7de078320e3fec7e130bf1089c458ee9ef2a`. GitHub's commits API
independently confirmed that exact current main SHA. The prior local DNS-01
security commit was excluded from the replay range; the canonical checkout was
not changed. Rebase completed without conflicts.

SSH fetch attempts stalled and were terminated; command-local URL attempts also
selected SSH through local configuration. The coordinator's successful fetch
and independent API SHA confirmation supplied the current base. No credentials
or persistent Git configuration were changed.

Upstream changed kube-prometheus-stack to 92.11.0 and Loki to 7.5.0. The ignored
chart dependencies were refreshed to those exact versions, then all four focused
commands above passed again with `S03_ALERTS_OK`. `git diff --check` passed.
The PR delta remains seven S03 files with empty production enrollment. Publishing
is delegated to the coordinator; this preparation made no push, PR, comment or
deployment. The full repository suite and live S04 checks remain outside this
focused verification.
