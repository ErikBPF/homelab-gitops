# Wazuh alerts in Loki

Date: 2026-11-28
Status: implemented; end-to-end delivery blocked by a monitoring-side policy

## Outcome and why

Wazuh stays the SIEM of record and the only syslog target. Rule-matched alerts
that Wazuh already writes to `alerts.json` now also reach Loki, so Grafana can
query network events next to host and cluster logs. Raw syslog does not go to
Loki, and `logall`/`logall_json` stay `no`.

## Blocker outside this slice

`platform/monitoring/templates/networkpolicy.yaml` (`allow-monitoring-runtime`)
admits ingress only from the monitoring namespace and from `traefik`. The Loki
gateway pods are in `monitoring`, so they drop the manager's pushes even though
the Wazuh egress rule allows them. This slice does not touch
`platform/monitoring`; the monitoring owner must add a separate NetworkPolicy
scoped to the gateway pods (`app.kubernetes.io/name: loki`,
`app.kubernetes.io/instance: loki`, `app.kubernetes.io/component: gateway`)
that admits the Wazuh namespace (`app: wazuh-manager`, `node-type: master`) on
TCP 8080. `allow-monitoring-runtime` has an empty pod selector, so do not add
`from` to that rule. Until the allow lands, the sidecar runs and logs
connection errors and Loki has no `job="wazuh-alerts"` stream. Do not widen
the Wazuh egress rule to compensate; egress is already correct.


## Changes

- `platform/wazuh/config/alloy-alerts.alloy`: Alloy pipeline. Tails
  `/wazuh-logs/alerts/alerts.json`, parses JSON, drops lines without
  `rule.id`, exposes `rule_id`, `level` and `agent` labels, and writes to
  `http://loki-gateway.monitoring.svc.cluster.local/loki/api/v1/push` with
  `job="wazuh-alerts"`.
- `platform/wazuh/kustomization.yaml`: `wazuh-alerts-alloy` ConfigMap from
  that file.
- `platform/wazuh/patches.yaml`: `unifi-log-ship` sidecar on
  `wazuh-manager-master` only. It binds the existing `wazuh-manager-master`
  PVC read-only at `/wazuh-logs`, mounts the config read-only, sets CPU/memory
  requests and a 256Mi memory limit, and sets pod `restartPolicy: Always`.
- `platform/wazuh/networkpolicy.yaml`: one egress rule from the Wazuh
  namespace to the Loki gateway pods in `monitoring` on TCP 8080.
- `tests/wazuh-loki-alerts.sh`: render assertions for the sidecar, read-only
  mounts, config contents, fixed job label, network rule, and `logall` state.
  Registered in `scripts/validate.sh`.

The sidecar is on the master only. In this Wazuh cluster the master analyses
events and writes alerts; workers forward them. Tailing the worker would
duplicate or miss alerts depending on cluster state.

## What this proves

- The rendered `wazuh-manager-master` StatefulSet has the sidecar, a read-only
  manager-PVC mount at `/wazuh-logs`, a read-only config mount, and an explicit
  memory limit. The worker StatefulSet has no such sidecar.
- The Alloy config parses and references `alerts.json`, not `archives.json`.
  `alloy fmt` validates the River syntax.
- A local `alloy run` with the same stages and `loki.echo` forwarded only the
  valid sample alert. It emitted `rule_id="504"`, `level="3"`,
  `agent="orion-canary"` and the event timestamp, and dropped a non-JSON line.
- Egress is limited to the Loki gateway pod port. The existing deny-all policy
  still applies to every other destination.
- Graph construction, not delivery, is proven. The monitoring ingress blocker
  above must land before any alert reaches Loki.
- Both `master.conf` and `worker.conf` keep `logall` and `logall_json` at `no`.


## What this does not prove

- Every Wazuh event reaches Loki. Only alerts that Wazuh already writes to
  `alerts.json` are shipped. Raw event archiving needs `logall_json: yes` and
  a separate storage/cost decision.
- Alerts written by a worker in a future topology are covered. Only the master
  is tailed today.
- Loki accepts the labels without creating an excessive series count. `agent`
  and `rule_id` cardinality follows the enrolled agents and rule set. Check
  Loki series counts after the first day.
- Wazuh indexer, dashboard and notification delivery are unchanged. This
  slice adds a consumer; it does not alter Wazuh routing.
- The pipeline was not run against a live `alerts.json`. Tests assert rendered
  configuration; runtime proof needs the recipe below.

## Verification recipe

1. Confirm the sidecar is ready and its logs are clean:

   ```bash
   kubectl --context homelab -n wazuh get pod wazuh-manager-master-0 \
     -o jsonpath='{range .spec.containers[*]}{.name}{"\n"}{end}'
   kubectl --context homelab -n wazuh logs wazuh-manager-master-0 \
     -c unifi-log-ship --tail=50
   ```

2. Wait for one alert, or use the next alert Wazuh produces, and read its
   labels from the source file:

   ```bash
   kubectl --context homelab -n wazuh exec wazuh-manager-master-0 \
     -c wazuh-manager -- tail -n 1 /var/ossec/logs/alerts/alerts.json \
     | jq '{rule_id: .rule.id, level: .rule.level, agent: .agent.name}'
   ```

3. Query Loki through the gateway:

   ```bash
   kubectl --context homelab -n monitoring port-forward svc/loki-gateway 8080:80
   curl -sG http://127.0.0.1:8080/loki/api/v1/query_range \
     --data-urlencode 'query={job="wazuh-alerts"}' \
     --data-urlencode 'limit=5' \
     | jq -r '.data.result[].stream | @json'
   ```

   Expected stream labels (at least these): `job="wazuh-alerts"`,
   `rule_id="<rule.id>"`, `level="<rule.level>"`, `agent="<agent.name>"`.
   Alloy also adds `filename="/wazuh-logs/alerts/alerts.json"` from `__path__`.

4. In Grafana Explore, select the Loki datasource and run:

   ```logql
   {job="wazuh-alerts"}
   {job="wazuh-alerts", rule_id="5710"}
   sum(count_over_time({job="wazuh-alerts"}[5m]))
   ```

## Decisions and limits

- The Alloy pin is `platform/alloy-metrics`'s mirrored
  `grafana/alloy:v1.21.2@sha256:89a5…b18`. `platform/monitoring/values.yaml`
  only pins the Grafana sidecar and Prometheus Operator images; it has no
  Alloy image.
- `restartPolicy: Always` is on the pod spec. Kubernetes rejects that field on
  a regular container; only native-sidecar init containers accept it there.
- Alloy positions live under `/tmp/alloy` inside the container, so a restart
  re-reads `alerts.json` from the start. The timestamp stage keeps the Wazuh
  event time, so a re-read is bounded instead of looking like fresh traffic:
  Loki suppresses only an immediately repeated line, re-ingests entries within
  roughly `max_chunk_age/2` (about one hour) and rejects older ones as
  `too_far_behind`. Expect a small duplicate window after a restart. Persisting
  positions would need a writable volume, which this slice forbids.
- The config is mounted with `subPath`, so kubelet does not refresh it. A
  pipeline change requires deleting `wazuh-manager-master-0`, which also
  restarts the manager. A reloader is not worth a second process here.
- This repository has no `docs/audits/` directory, so this one-pager follows
  the existing flat `docs/` convention.

## Rollback

Revert the change and sync the previous published revision. Removing the
sidecar returns the manager pod to its prior single-container shape. Wazuh
configuration is untouched, so no Wazuh data migration or reindex is needed.
The extra NetworkPolicy rule is inert without the sidecar and can stay or be
removed with the same revert.
