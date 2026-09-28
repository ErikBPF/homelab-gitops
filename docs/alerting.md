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
