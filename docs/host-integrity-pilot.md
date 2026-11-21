# Endeavour integrity pilot: GitOps handoff

## Outcome and sequencing

Source is staged for exactly three Endeavour expectations: `signatures`, `store`,
and `av`. This makes a missing configured check distinguishable from a sleeping
workstation. It does not enroll any other host or change alert thresholds.
The coordinator must first observe the compatible collector's three identities
and required gauges in Prometheus. **Do not merge enrollment before that proof:**
the live monitoring application has automated sync with pruning and self-healing,
so merging to main can activate it without a manual command. After that gate,
publish the reviewed revision and verify the automatic reconciliation; use
`just sync REVISION monitoring` only when an explicit operation is needed.
Before submission, verify that exact 40-character revision is published on main,
using the remote main SHA or a fetched main ancestry check. The recipe validates
SHA format, not publication or main membership. Do not sync a working tree or a
moving branch. Host jobs activate only in the coordinator's authorized sequence.

Endeavour remains outside the always-on host-absence list. Integrity health uses
its current healthy `up{job="integrations/unix"}`. Sleep suppresses health pages;
wake with expired complete coverage still warns. An awake failed scrape belongs
to `host-alloy-down`. Findings remain independently visible while their samples
exist. Losing samples may resolve Grafana without producer recovery.

## Read-only preflight evidence

On 2026-11-21 around 13:19 UTC, using the existing owner access:

- `just grafana-alert-status` successfully queried Grafana through the `homelab`
  Kubernetes context. Its script consumes the monitoring admin credential in
  memory and prints only the curated inventory. Never enable shell tracing.
- Grafana's authenticated Prometheus proxy returned Endeavour `up=1`, no
  `host_integrity_state` series, and no `host_integrity_expected` series. This is
  a **pre-collector snapshot**, not proof of deployed enrollment or coverage.
- `GET /api/prometheus/grafana/api/v1/rules` showed all five integrity rules
  inactive, healthy and recently evaluated. `GET /api/v1/provisioning/alert-rules/UID`
  confirmed the native rules exist with five-minute pending periods.
- Read-only SSH to Vanguard showed the independent dead-man timer active,
  last trigger 13:19:35 UTC, successful service result, zero consecutive probe
  failures, and its separate webhook file present. No credential value was read
  or printed. Owner source probes the public HA endpoint from Vanguard; this
  establishes current healthy offsite probing, not outage notification receipt.

Use `GET /api/datasources/proxy/uid/prometheus/api/v1/query?query=...` to check
only the named pilot series. Record check identities, state, fresh attempt and
complete timestamps, counts, and expected-pair count. Do not copy raw receipts,
scan paths, database signatures, webhook values or unrelated alert data into the
coordination record. The current monitoring access is available; no new bot or
credential is required for these reads.

## Sync boundary and observation history

The authoritative [sync recipe](../scripts/sync.sh) reads only the named Argo
application, rejects an existing operation, then submits an atomic JSON Patch
with a resource-version test. `just sync REVISION monitoring` targets monitoring
alone; it does not request a root/global sync, force, or pruning. It inherits
that application's sync options and reconciles its resources at the selected
revision. Submission success is not completion: separately verify the operation
phase, deployed sync revision and application health before producer cases.

Do not confuse that command's lack of force/prune flags with live automation.
Read-only inspection confirmed `automated: {prune: true, selfHeal: true}`,
`Synced/Healthy` at `c2c127818dd71607d257076590a3c0a94febca3c`, last operation
succeeded at 2026-11-21 13:06:12 UTC, with no pending operation. The source
Application manifest only lists sync options; the observed live policy controls
this rollout. Recheck it immediately before activation.

Both source and the deployed Prometheus resource configure `retention: 15d`
and `retentionSize: 25GB`. At 2026-11-21 13:26 UTC, the backend's
`prometheus_tsdb_lowest_timestamp_seconds` reported approximately 1,344,417
seconds (15.56 days) of retained TSDB history. This supports using the existing
Prometheus history and protected producer receipts for seven-day observation;
no new scheduler is needed. It does not prove that every pilot series already
has that history. The size ceiling can shorten future retention under pressure,
so check the actual available interval again at closeout and record gaps.

## Notification and recovery receipt

The coordinator will use the existing native `host-integrity-finding` rule,
not a separate delivery canary. After collector and enrollment verification,
Desktop's authorized helper emits a harmless synthetic finding through the same
collector with partial state; it must never claim complete Downloads coverage.
Record the exact instance/check, synthetic receipt identifier, start time,
published revisions, recovery deadline and stop condition before that action.
Observe the real rule's five-minute pending period, firing identity and native
Discord notification. Do not send direct webhook messages or silence incidents.

Recovery requires a real Downloads run with zero new findings plus an audited
clear of only the identified synthetic finding. Any actual finding blocks that
clear and needs investigation. A complete run must advance the complete-coverage
timestamp. A partial run instead records a fresh attempt and partial state while
leaving the previous complete timestamp unchanged; zero new findings does not
make that partial coverage complete. Record which outcome occurred. Clearing an
audited synthetic finding may resolve the finding rule while a truthful
incomplete-coverage warning remains. Record the real rule's normal state and
resolved notification without calling unresolved coverage recovered.
The existing route has a 30-second group wait and five-minute group interval;
allow for those separately from the rule's pending interval. Never use rule
removal, missing samples or expired lookback as recovery evidence.

The coordinator may also temporarily omit and restore the named check's metric
file through the owner helper, while proving host telemetry remains healthy,
to exercise the real missing-check rule. Keep that omission short and restore
it before proceeding. Stale coverage can be checked offline; an isolated copied
rule with a time-offset fixture would not prove real production expiry and must
not be presented as that evidence. Stop later cases on failed recovery or after
three repairs of the same failure, preserving the protected receipts.

No available owner tool provides attributable Discord message receipt retrieval.
Grafana active-alert/rule APIs establish evaluator state; delivery counters only
support notification attempts amid unrelated traffic. A receiver-status endpoint
probe returned HTTP 404. Do not create arbitrary bot credentials or infer receipt
from those observations. Ask the operator to confirm **both** messages with:
channel, exact title/rule identity and pilot labels, firing UTC and message link
(or message ID), resolved UTC and message link (or ID), and whether the wording
was actionable. Correlate the resolved receipt with the fresh producer evidence.
The prior historical drill still has no recorded human receipt and cannot close
this pilot. No external-outage drill is included.

## Validation and remaining gates

The enrollment assertion first failed because production source was still empty.
After staging the three pairs, the renderer checks, real recording-rule evaluation,
sleep/wake identity fixtures, PromQL, template and status tests passed with
`S04_ALERT_PREFLIGHT_OK`. Empty-list rendering remains covered. Validation used the
existing pinned temporary environment from the S03 receipt and exact chart locks.

Pending: collector/backend proof, published revisions, coherent activation,
authorized producer cases, native firing/resolved receipts, recovery, and the
seven-day cadence/cost/noise observation. Source tests and one successful probe
do not satisfy those gates. For durable withdrawal, publish a reviewed source
revert of enrollment, verify its new main SHA, then observe reconciliation or
sync that exact new SHA on monitoring and verify the deployed result. Merely
syncing an older revision is not durable: automated reconciliation against main
can restore enrollment. If an emergency requires temporary automation control,
use a separately verified owner procedure scoped to the monitoring application,
record its previous policy, and restore it deliberately; do not globally suppress
reconciliation. Retain investigation evidence and never describe alert resolution
caused by enrollment withdrawal as recovered coverage.
