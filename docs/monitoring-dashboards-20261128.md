# Monitoring dashboards and gateway health

Date: 2026-11-28
Branch: `feat/monitoring-dashboards`
Worktree: `worktrees/monitoring-dashboards-20261128`
Scope: `platform/monitoring/**`, its tests and this page.

## Outcome

The monitoring Application now provisions Grafana dashboards through the
dashboard sidecar. Two generated dashboards cover UniFi gateway health and
goflow2 IPFIX collector health. A Grafana OpenSearch datasource with uid
`wazuh` points at the Wazuh indexer over HTTPS. A new `gateway-health` alert
group adds five rules for gateway reachability, load, memory, WAN state and
IPFIX flow loss.

## Changed files

- `platform/monitoring/values.yaml`: enable `sidecar.dashboards` with
  `grafana_dashboard=1`, `searchNamespace: monitoring`, and the existing
  sidecar image pin and resources. Add the `wazuh` datasource using environment
  interpolation.
- `platform/monitoring/templates/grafana-dashboards.yaml`: ConfigMap from
  `files/dashboards/*.json`.
- `platform/monitoring/files/dashboards/gateway-health.json` and
  `ipfix-flows.json`: dashboard definitions.
- `platform/monitoring/templates/externalsecret.yaml`: add
  `WAZUH_INDEXER_USERNAME`, `WAZUH_INDEXER_PASSWORD` and `WAZUH_INDEXER_CA`
  from `platform/wazuh/indexer-cred` and `platform/wazuh/indexer-certs`.
- `platform/monitoring/templates/blackbox-probes.yaml` and
  `blackbox-exporter.yaml`: add the `https_insecure` module and a
  `gateway-management` Probe for `https://192.168.10.1`.
- `platform/monitoring/files/alerting/rules.yaml`: new `gateway-health` group.
- `tests/monitoring-dashboards.sh` and `scripts/validate.sh`: new contract
  test and registration.
- `docs/alerting.md`: gateway-health runbook section.

## Verification evidence

`helm template monitoring platform/monitoring` returns 0. The rendered
datasource block is:

```yaml
- access: proxy
  basicAuth: true
  basicAuthUser: $WAZUH_INDEXER_USERNAME
  jsonData:
    timeField: '@timestamp'
    tlsAuthWithCACert: true
  name: Wazuh
  secureJsonData:
    basicAuthPassword: $WAZUH_INDEXER_PASSWORD
    tlsCACert: $WAZUH_INDEXER_CA
  type: opensearch
  uid: wazuh
  url: https://wazuh-indexer-0.wazuh-indexer.wazuh.svc.cluster.local:9200
```

Rendered `gateway-health` expressions:

```text
gateway-management-probe-down (5m):
min by (instance) (probe_success{job="blackbox-gateway"}) < bool 1 or absent(probe_success{job="blackbox-gateway"})

gateway-load-high (15m):
max by (name, site_name) (unifi_device_load_average_5) > 2.5

gateway-memory-high (15m):
max by (name, site_name) (unifi_device_memory_utilization_ratio) > 0.9

gateway-wan-degraded (5m):
min by (name) (unifi_device_wan_speed_bps) == bool 0

ipfix-collector-down (10m):
(max by (job) (up{job="goflow2"}) < bool 1 or absent(up{job="goflow2"}))
or (
  (absent(goflow2_flow_process_nf_total{job="goflow2"})
  or (sum(rate(goflow2_flow_process_nf_total{job="goflow2"}[5m])) == bool 0))
  and on() (max by (job) (up{job="goflow2"}) == 1)
  and on() (max(unifi_device_uptime_seconds) > 0)
)
```

`bash tests/monitoring-dashboards.sh` prints
`monitoring dashboards contract: ok`. The test checks sidecar settings,
dashboard JSON, the ConfigMap label, the gateway Probe, the Wazuh datasource,
datasource uid coverage, rule runbook URLs and the absence of a flow-burst rule.

`promtool check rules` accepts the five rendered expressions (`SUCCESS: 5 rules
found`). `tests/monitoring-contract.sh` and `tests/monitoring-promql.py` pass
under `nix shell nixpkgs#prometheus.cli` with PyYAML on `PYTHONPATH`.

Limits: rendering is not proof that Grafana reaches the Wazuh datasource or
that unpoller/goflow2 produce these series. The blackbox exporter must restart
to load the new `https_insecure` module; this slice adds no reloader or
checksum annotation.

## Unverified metric names

The `unifi_*` prefix is not confirmed against the deployed unpoller arguments.
Upstream code defaults the namespace to `unpoller`, while the slice brief names
`unifi_*`. The dashboard and rules mark the namespace assumption with `verify`.
Upstream-confirmed names: `unifi_device_cpu_utilization_ratio`,
`unifi_device_memory_utilization_ratio`, `unifi_device_load_average_5`,
`unifi_device_uptime_seconds`, `unifi_device_storage`,
`unifi_device_wan_receive_bytes_total`, `unifi_device_wan_transmit_bytes_total`,
`unifi_device_wan_speed_bps`, `unifi_device_uptime_seconds`, `unifi_site_stations`.
unpoller 2.34.0 exposes no WAN status gauge and no WAN error counters; the
inventory in the unpoller slice is authoritative.

`goflow2_flow_bytes_total` with `src_addr`, `dst_addr` or `dst_port` is not a
stock goflow2 metric. The three top-N panels are tagged `verify` and stay empty
until a flow-record Prometheus producer supplies those dimensions. Native
goflow2 metrics used elsewhere are `goflow2_flow_process_nf_total`,
`goflow2_flow_traffic_bytes_total`, `goflow2_flow_traffic_packets_total`,
`goflow2_flow_process_nf_flowset_records_total` and
`goflow2_flow_decoder_error_total`.

## Decisions

- Use the indexer pod DNS, not the `indexer` Service name. The issued
  certificate SAN covers `wazuh-indexer-0.wazuh-indexer.wazuh.svc.cluster.local`
  but not `indexer.wazuh.svc.cluster.local`; CA verification therefore needs the
  pod name.
- Copy the Vault credential and CA into `monitoring-secrets`. A Grafana pod in
  `monitoring` cannot reference the `wazuh` namespace Secret.
- Probe the gateway with `https_insecure` and accept 200/302/401/403. The
  management certificate is device-local; the probe measures reachability.
- Thresholds 2.5 load and 90% memory are initial values, not measured limits.
- Do not alert on flow-volume bursts.

## Known pre-existing defect

`platform/monitoring/Chart.yaml` declares `kube-prometheus-stack` version
`92.22.0`. The vendored chart in the canonical checkout is `88.5.3`. This is
unchanged and outside this slice. The coordinator must decide whether to pin
the declaration or refresh the vendored chart before delivery.

## Rollback

Revert the files above. If the rules were already synced, Grafana keeps removed
file-provisioned rules; add the five UIDs to `deleteRules` before removing the
group. Disabling `sidecar.dashboards` stops dashboard loading but does not delete
the ConfigMap or Grafana folders. Reverting `additionalDataSources` removes the
Wazuh datasource; remove its secret keys in the same revert.
