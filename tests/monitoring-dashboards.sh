#!/usr/bin/env bash
# Contract for the generated Grafana dashboards, the Wazuh datasource and the
# gateway-health alert group. Rendering is the acceptance surface; live scrape
# availability and datasource reachability need a separate runtime check.
set -euo pipefail

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"

values=platform/monitoring/values.yaml
rules=platform/monitoring/files/alerting/rules.yaml

# Dashboards must use the same sidecar image and resources as alerts.
test "$(yq -r '.kube-prometheus-stack.grafana.plugins | join(",")' "$values")" = grafana-opensearch-datasource
test "$(yq -r '.kube-prometheus-stack.grafana.sidecar.dashboards.enabled' "$values")" = true
test "$(yq -r '.kube-prometheus-stack.grafana.sidecar.dashboards.label' "$values")" = grafana_dashboard
test "$(yq -r '.kube-prometheus-stack.grafana.sidecar.dashboards.labelValue' "$values")" = 1
test "$(yq -r '.kube-prometheus-stack.grafana.sidecar.dashboards.searchNamespace' "$values")" = monitoring
test "$(yq -r '.kube-prometheus-stack.grafana.sidecar.image.registry' "$values")" = harbor.homelab.pastelariadev.com/quay
yq -r '.kube-prometheus-stack.grafana.sidecar.image.sha' "$values" | grep -Eq '^[0-9a-f]{64}$'
test "$(yq -r '.kube-prometheus-stack.grafana.sidecar.resources.limits.memory' "$values")" = 128Mi

# Every dashboard file must be valid JSON with a unique uid.
uids=$(mktemp)
for file in platform/monitoring/files/dashboards/*.json; do
  jq -e . "$file" >/dev/null
  jq -r '.uid' "$file" | grep -Eq '^[a-z0-9-]+$'
  jq -r '.panels | length' "$file" | grep -Eq '^[1-9][0-9]*$'
  jq -r '.panels[].targets[]?.datasource.uid' "$file" | grep -Evq '^prometheus$' && {
    echo "$file: dashboard target must use the prometheus uid" >&2
    exit 1
  }
  jq -r '.uid' "$file" >>"$uids"
done
test "$(sort -u "$uids" | wc -l)" -eq "$(wc -l <"$uids")"
rm -f "$uids"
jq -e '.tags | index("verify")' platform/monitoring/files/dashboards/gateway-health.json >/dev/null
jq -e '.tags | index("verify")' platform/monitoring/files/dashboards/ipfix-flows.json >/dev/null
jq -e '.panels[] | select((.description // "") | startswith("verify: unifi_"))' platform/monitoring/files/dashboards/gateway-health.json >/dev/null
# goflow2 exposes receipt/decode counters only: there is no src/dst dimension
# (that needs record export), so a panel querying one would render empty forever.
if jq -e '.panels[].targets[]? | select((.expr // "") | test("goflow2_flow_bytes_total"))' platform/monitoring/files/dashboards/ipfix-flows.json >/dev/null; then
  echo "ipfix dashboard references a flow-record metric goflow2 does not expose" >&2
  exit 1
fi
jq -e '.description | contains("record export")' platform/monitoring/files/dashboards/ipfix-flows.json >/dev/null

render=$(mktemp)
trap 'rm -f "$render"' EXIT
helm template monitoring platform/monitoring >"$render"

# The generated dashboard ConfigMap carries the sidecar label and both files.
test "$(yq -r 'select(.kind == "ConfigMap" and .metadata.name == "grafana-dashboards") | .metadata.labels.grafana_dashboard' "$render")" = 1
test "$(yq -r 'select(.kind == "ConfigMap" and .metadata.name == "grafana-dashboards") | .data | keys | length' "$render")" = 2
for key in gateway-health.json ipfix-flows.json; do
  yq -e "select(.kind == \"ConfigMap\" and .metadata.name == \"grafana-dashboards\") | .data[\"$key\"]" "$render" >/dev/null
done
grep -Fq 'grafana_dashboard: "1"' platform/monitoring/templates/grafana-dashboards.yaml

# Gateway probe target and reachability module.
test "$(yq -r 'select(.kind == "Probe" and .metadata.name == "gateway-management") | .spec.jobName' "$render")" = blackbox-gateway
test "$(yq -r 'select(.kind == "Probe" and .metadata.name == "gateway-management") | .spec.module' "$render")" = https_insecure
test "$(yq -r 'select(.kind == "Probe" and .metadata.name == "gateway-management") | .spec.targets.staticConfig.static[0]' "$render")" = https://192.168.10.1

# The Wazuh datasource exists, uses TLS, and takes credentials from the
# environment supplied by the monitoring secret.
wazuh=$(yq -r '.kube-prometheus-stack.grafana.additionalDataSources[] | select(.uid == "wazuh")' "$values")
test "$(yq -r '.type' <<<"$wazuh")" = grafana-opensearch-datasource
test "$(yq -r '.url' <<<"$wazuh")" = https://wazuh-indexer-0.wazuh-indexer.wazuh.svc.cluster.local:9200
test "$(yq -r '.jsonData.timeField' <<<"$wazuh")" = '@timestamp'
test "$(yq -r '.jsonData.tlsAuthWithCACert' <<<"$wazuh")" = true
test "$(yq -r '.basicAuthUser' <<<"$wazuh")" = '$WAZUH_INDEXER_USERNAME'
test "$(yq -r '.secureJsonData.basicAuthPassword' <<<"$wazuh")" = '$WAZUH_INDEXER_PASSWORD'
test "$(yq -r '.secureJsonData.tlsCACert' <<<"$wazuh")" = '$WAZUH_INDEXER_CA'
if yq -r '.secureJsonData.basicAuthPassword' <<<"$wazuh" | grep -Evq '^\$[A-Z_]+$'; then
  echo "wazuh datasource password is not environment-interpolated" >&2
  exit 1
fi
for key in WAZUH_INDEXER_USERNAME WAZUH_INDEXER_PASSWORD WAZUH_INDEXER_CA; do
  grep -Fq "secretKey: $key" platform/monitoring/templates/externalsecret.yaml
done

# Datasource uids referenced by every rule must exist in the rendered provisioning.
datasources=$(mktemp)
trap 'rm -f "$render" "$datasources"' EXIT
yq -r 'select(.kind == "ConfigMap" and .metadata.name == "monitoring-kube-prometheus-grafana-datasource") | .data["datasource.yaml"]' "$render" >"$datasources"
yq -e '.datasources[] | select(.uid == "wazuh" and .type == "grafana-opensearch-datasource")' "$datasources" >/dev/null
allowed="__expr__ $(yq -r '.datasources[].uid' "$datasources" | tr '\n' ' ')"
while IFS= read -r uid; do
  grep -qw -- "$uid" <<<"$allowed" || {
    echo "rule references undeclared datasource uid: $uid" >&2
    exit 1
  }
done < <(for file in platform/monitoring/files/alerting/*.yaml; do
  yq -r '.groups[]?.rules[]?.data[]?.datasourceUid // ""' "$file"
done | sed '/^$/d' | sort -u)

# Gateway alert group contract.
gateway=platform/monitoring/files/alerting/rules.yaml
test "$(yq -r '.groups[] | select(.name == "gateway-health") | .rules | length' "$gateway")" = 5
for uid in gateway-management-probe-down gateway-load-high gateway-memory-high gateway-wan-degraded ipfix-collector-down; do
  yq -e ".groups[] | select(.name == \"gateway-health\") | .rules[] | select(.uid == \"$uid\") | .annotations.runbook_url" "$gateway" >/dev/null
  yq -e ".groups[] | select(.name == \"gateway-health\") | .rules[] | select(.uid == \"$uid\") | .annotations.summary" "$gateway" >/dev/null
  yq -e ".groups[] | select(.name == \"gateway-health\") | .rules[] | select(.uid == \"$uid\") | .labels.severity" "$gateway" >/dev/null
  test "$(yq -r ".groups[] | select(.name == \"gateway-health\") | .rules[] | select(.uid == \"$uid\") | .condition" "$gateway")" = C
  test "$(yq -r ".groups[] | select(.name == \"gateway-health\") | .rules[] | select(.uid == \"$uid\") | .data | map(.refId) | join(\"\")" "$gateway")" = ABC
done
grep -Fq 'probe_success{job="blackbox-gateway"}' "$gateway"
grep -Fq 'unifi_device_load_average_5' "$gateway"
grep -Fq 'unifi_device_memory_utilization_ratio' "$gateway"
grep -Fq 'unifi_device_wan_speed_bps' "$gateway"
# The no-flow rule must be gated on the WAN link, not on generic uptime.
test "$(yq -r '.groups[] | select(.name == "gateway-health") | .rules[] | select(.uid == "ipfix-collector-down") | .data[0].model.expr' "$gateway" | grep -c 'unifi_device_wan_speed_bps')" -ge 1
# Blackbox modules live in a file, and the Deployment must roll on change.
grep -Fq 'checksum/blackbox-config' platform/monitoring/templates/blackbox-exporter.yaml
test -f platform/monitoring/files/blackbox.yml
# Envelope bytes per decoded record, not per message.
jq -e '.panels[] | select(.title == "Envelope bytes per record") | .targets[0].expr | contains("flowset_records_total")' platform/monitoring/files/dashboards/ipfix-flows.json >/dev/null
jq -e '.panels[] | select(.title == "Flow rate") | .targets[0].expr | contains("flowset_records_total")' platform/monitoring/files/dashboards/ipfix-flows.json >/dev/null
grep -Fq 'goflow2_flow_process_nf_total{job="goflow2"}' "$gateway"
# unpoller 2.34.0 exposes no wan status or error counters, and no per-interface
# rate gauges. These names must not reappear; the inventory in
# docs/unpoller-metrics.md (homelab-gitops platform/unpoller) is authoritative.
for phantom in unifi_device_wan_status unifi_device_wan_receive_rate_bytes \
  unifi_device_wan_transmit_rate_bytes unifi_device_wan_receive_errors_total \
  unifi_device_wan_transmit_errors_total; do
  if grep -rFq "$phantom" "$gateway" platform/monitoring/files/dashboards; then
    echo "phantom unpoller metric referenced: $phantom" >&2
    exit 1
  fi
done

if grep -Eiq 'flow.*burst|burst.*flow' "$gateway"; then
  echo "flow-volume burst alert is not wanted" >&2
  exit 1
fi

echo "monitoring dashboards contract: ok"
