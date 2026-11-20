# Alert response

Grafana rules, delivery and inventory belong to `homelab-gitops`. Start with
`just grafana-alert-status` here and the notification's Rule link. An unavailable
inventory is a monitoring failure, never an empty queue.

Notifications show curated conditions, severity, available host/cluster identity,
and UTC firing/end times. `since` is the firing timestamp, not the underlying
failure's beginning; each rule also has an evaluation interval and pending period.
The title counts the complete group; the body shows up to four firing and two
resolved instances. Open Rule for omitted instances, evaluated values, thresholds,
pending periods, and state history. Security-event values stay inside their
authorized diagnostic source.

Resolved means Grafana stopped firing. It does not prove that a backup succeeded,
that a workload recovered, or that telemetry is present: a missing series or rule
change can also stop an instance. Check the producer and fresh metrics before
closing. An OOM/authentication event leaving its lookback window does not undo it.

| Rule family | Impact / owner | First safe check |
|---|---|---|
| Container health, restarts, missing Docker identity | Workload or exporter failure; `servarr` for Discovery/legacy Compose, `desktop-nixos` for host exporters | Check host and exporter reachability, then the named container. Missing telemetry does not establish workload failure. |
| Host metrics, memory, OOM, load, swap, inodes, filesystem capacity/prediction; Voyager scrape/storage | Host reliability or exhausted capacity; `desktop-nixos` | Check the named host's Alloy/node exporter and recent journal; inspect memory/load or `df -h` / `df -i`. Predictions describe a trend, not a scheduled outage. Never prune or delete data directly from the alert. |
| systemd failed / security control inactive | Named job failed or control unavailable; host unit owner in `desktop-nixos` or `servarr` | On the named host inspect `systemctl status UNIT` and `journalctl -u UNIT`; inspect a user unit with `--user` when applicable. Fix the cause before restarting; `reset-failed` alone is not recovery. |
| AdGuard, cloudflared, LiteLLM scrape; Pangolin redundancy; Buzz relay | Service telemetry unavailable or redundancy reduced; `homelab-gitops` for Kubernetes, `servarr` for legacy Compose, `desktop-nixos` for host ingress | Check the Rule query's scrape target and owning workload readiness before diagnosing a user-facing outage. |
| Tofu-state / OpenBao / Kepler / Buzz backup freshness, Voyager restic check, restore drills | Recovery evidence expired; backup job owner (`servarr` for legacy jobs, `homelab-gitops` for Kubernetes, `desktop-nixos` for host jobs) | Compare each named local/offsite/B2 leg's last success and timer/job status. Inspect logs privately. A fresh local backup does not prove offsite coverage; a fresh backup does not prove restoration. Run drills only through the owning isolated procedure. |
| Sops runtime readiness | Host bootstrap dependencies unavailable; `desktop-nixos` | Inspect the affected host's Sops service status and readiness metric. Check existence/permissions only; never print `/run/secrets` contents. |
| OpenBao sealed, authenticated probe failed/stale, Vault Agent required renders missing | Runtime secrets unavailable or required renders not ready; `desktop-nixos` owns Discovery's native `openbao.service` and `vault-agent.service` | Check service health, agent status and required-render readiness. The authenticated probe requires a successful token lookup; HTTP 200 health alone does not prove auditing works. Its last-attempt timestamp must be within 10m; failure, missing metrics or stale evidence alerts after 5m. Inspect the existing `openbao-seal-probe` timer and audit ownership through `desktop-nixos` diagnostics before using the owner's unseal/restart procedure. Unchanged rendered contents retain their file mtime; old mtime alone does not establish stale credentials or failed rendering. Do not display tokens, unseal keys, rendered files, or full secret API responses. |
| Kepler offsite Restic transport | Offsite repository inaccessible; legacy backup owner `servarr` | Check backup-container connectivity to Voyager and repository health using its existing credential path. Do not prune, reinitialize, or copy credentials. |
| Kubernetes node/PVC/job/deployment/StatefulSet/HPA | Scheduling, storage or workload capacity degraded; `desktop-nixos` for substrate, workload owner for application | Use the Rule's cluster, namespace and object identity for `kubectl describe` and recent events. For HPA, inspect desired replicas and scaling conditions; being at maximum alone does not prove unsatisfied demand. |
| Argo application unhealthy/out of sync | Deployment unhealthy or desired/live drift; application's owning repo | Inspect application health/sync status and its diff. If dependencies fail, check ExternalSecret conditions and SecretStore readiness before workload changes. Never sync unexplained differences or print Secret data. |
| Kubernetes Alloy OOM/restart/readiness/backlog/retries, etcd metrics missing | Telemetry delivery or cluster observability degraded; `homelab-gitops` for monitoring, `desktop-nixos` for substrate | Check monitoring pod status/events, scrape health and queue trends. Confirm whether an OOM is a new event or retained termination reason. Keep telemetry failures distinct from workload outages. |
| Journal delivery failures, SSH burst/non-whitelisted login | Security visibility loss or authentication event; host security owner `desktop-nixos`, legacy Discovery diagnostics `servarr` | Follow the notification's security runbook; inspect authorized journal/Loki data privately. Do not post IPs, usernames, fingerprints, log lines or credentials to Discord or audit evidence. |
| Prometheus/Loki datasource health, Grafana delivery failures | Detection or notification path degraded; `homelab-gitops` | Check datasource/pod health and notification history privately. A Discord-delivery alert uses the same path that may be broken; verify the independent Vanguard dead-man and operator inventory. |
| Cognee API/PVC/backup | API unavailable, storage pressure or expired backup evidence; `homelab-gitops` | Inspect Cognee deployment/events, volume capacity and backup CronJob/Job history. ConfigMap creation time alone does not prove restorable data. Use the existing isolated restore procedure. |

Keep truthful failures visible while the owner repairs them. Evidence records
revision, rule UID/title, class, timestamps, delivery/recovery result and owner;
omit credentials and raw security-event values. Source-render tests cannot prove
Discord receipt or operator usefulness. Those require a staffed, bounded
firing/resolved delivery check.

## Host integrity

The five `host-integrity-*` rules report findings, incomplete coverage, expired
coverage, old signature databases, and missing check evidence. `instance` names
the fleet host; `check` is `signatures`, `store`, or `av`. Host producers belong to
`desktop-nixos`; GitOps owns these queries and notification provisioning.

Enrollment is deliberately empty in `platform/monitoring/values.yaml`:
`hostIntegrity.expectedChecks: []`. S04 must verify producer compatibility and
explicitly enroll each supported `{instance, check}` pair, including any decision
about Endeavour. No exclusion or unenrolled host is evidence of a clean scan.
The chart creates `host_integrity_expected` recording rules through the existing
Prometheus operator's release selector. This reviewed GitOps list remains present
when a producer disappears; producer metrics never determine enrollment.

| Condition | Meaning and first check |
|---|---|
| Finding | A retained positive result needs private investigation. Inspect the owner's protected receipt; do not include paths or signatures in notifications. A later clean, partial, or failed run does not clear this metric. Only reviewed producer-side resolution clears it. |
| Incomplete | State is partial or failed. Inspect the attempt's coverage, skipped items and errors; repair the cause before rerunning. This suppresses the complete-age warning for that check. |
| Stale | Complete coverage is older than 48 hours for store/signatures or eight days for AV. Equality is tolerated; exceeding the boundary warns. Running does not renew complete coverage. Check timers and protected receipts. |
| Signature age | Actual selected signature database publication is older than 48 hours or unknown (zero). Successful updater execution and fresh publication of metrics do not establish a fresh database. |
| Missing check | An enrolled check has never run or lacks required gauges while its host's node-exporter scrape is healthy. Missing signature-database evidence is included. Inspect collector publication and enrollment before claiming coverage. |

Coverage-health rules require a current healthy `up{job="integrations/unix"}`
for the host. Existing host-loss/scrape-down rules own whole-host failures;
dedicated datasource rules own query failures. The existing always-on host-loss
inventory excludes Endeavour; S04 must reconcile its intermittent availability
and host-loss ownership before enabling expectations there. These rules keep prior state on
query error and treat an empty result as OK. Findings remain visible independently
of partial/failed state and host-health gating while their samples exist.
All five rules evaluate every minute with a five-minute pending period.

A producer-latched finding is not a durable Grafana incident store: missing series,
retention expiry, disabled enrollment, or rule changes can resolve an alert without
recovery. Prometheus currently retains samples for 15 days; the recording rules
preserve expectations, not historical findings. Verify fresh producer evidence
before closing an incident. Offline tests cannot prove Grafana pending duration,
missing-series lifecycle, Discord delivery, or operator response; S04 owns those
staffed checks. Never inject live findings as part of source validation.

The expectation namespace is trusted monitoring configuration. S01 rejects
`host_integrity_*` injection through legacy textfile input, but these queries do
not authenticate a remote-write client: a writer with access to Prometheus could
forge metrics, including expectation series. Review receiver access separately;
do not grant an untrusted exporter write access based on these rules.
