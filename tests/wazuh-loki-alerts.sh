#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
app=$root/platform/wazuh
render=$(mktemp)
trap 'rm -f "$render"' EXIT
kubectl kustomize "$app" >"$render"

sidecar='select(.kind == "StatefulSet" and .metadata.name == "wazuh-manager-master") | .spec.template.spec.containers[] | select(.name == "unifi-log-ship")'

# The alert shipper is a sidecar on the master only: the master analyses and
# writes alerts, workers forward.
test "$(yq -r 'select(.kind == "StatefulSet" and .metadata.name == "wazuh-manager-master") | .spec.template.spec.restartPolicy' "$render")" = Always
test "$(yq -r "$sidecar | .name" "$render")" = unifi-log-ship
test "$(yq -r "$sidecar | .resources.limits.memory" "$render")" = 256Mi
test "$(yq -r 'select(.kind == "StatefulSet" and .metadata.name == "wazuh-manager-worker") | .spec.template.spec.containers[] | select(.name == "unifi-log-ship") | .name' "$render")" = ""

# Reuse the Alloy pin already mirrored for alloy-metrics.
alloy_digest=$(yq -r '.alloy.image.digest' "$root/platform/alloy-metrics/values.yaml")
image=$(yq -r "$sidecar | .image" "$render")
[[ $image == *@$alloy_digest ]]

# No writable mounts: config read-only, alert log read-only on a distinct path.
mounts=$(yq -o=json "$sidecar | .volumeMounts" "$render")
jq -e 'length > 0 and all(.[]; .readOnly == true)' <<<"$mounts" >/dev/null
jq -e 'any(.[]; .name == "wazuh-manager-master" and .mountPath == "/wazuh-logs" and .readOnly == true)' <<<"$mounts" >/dev/null
jq -e 'any(.[]; .name == "wazuh-alerts-alloy" and .mountPath == "/etc/alloy/config.alloy" and .readOnly == true)' <<<"$mounts" >/dev/null
test "$(yq -r 'select(.kind == "StatefulSet" and .metadata.name == "wazuh-manager-master") | .spec.template.spec.volumes[] | select(.name == "wazuh-alerts-alloy") | .configMap.name' "$render")" = wazuh-alerts-alloy

# ConfigMap carries the pipeline: alerts.json only, labels, drop, fixed job.
test "$(yq -r 'select(.kind == "ConfigMap" and .metadata.name == "wazuh-alerts-alloy") | .metadata.namespace' "$render")" = wazuh
config=$(yq -r 'select(.kind == "ConfigMap" and .metadata.name == "wazuh-alerts-alloy") | .data["config.alloy"]' "$render")
grep -Fq '/wazuh-logs/alerts/alerts.json' <<<"$config"
grep -Fq 'rule.id' <<<"$config"
grep -Fq 'rule.level' <<<"$config"
grep -Fq 'agent.name' <<<"$config"
grep -Fq 'stage.json' <<<"$config"
grep -Fq 'stage.drop' <<<"$config"
grep -Fq 'stage.labels' <<<"$config"
grep -Fq 'stage.timestamp' <<<"$config"
grep -Eq 'source[[:space:]]*=[[:space:]]*"rule_id"' <<<"$config"
grep -Eq 'expression[[:space:]]*=[[:space:]]*"\^\$"' <<<"$config"
grep -Eq 'format[[:space:]]*=[[:space:]]*"2006-01-02T15:04:05\.000-0700"' <<<"$config"
grep -Eq 'rule_id[[:space:]]*=[[:space:]]*""' <<<"$config"
grep -Eq 'level[[:space:]]*=[[:space:]]*""' <<<"$config"
grep -Eq 'agent[[:space:]]*=[[:space:]]*""' <<<"$config"
grep -Fq 'http://loki-gateway.monitoring.svc.cluster.local/loki/api/v1/push' <<<"$config"
grep -Fq 'job = "wazuh-alerts"' <<<"$config"
if grep -Fq 'archives.json' <<<"$config"; then
  echo 'Alloy config must not tail archives.json' >&2
  exit 1
fi

# Minimal egress: the manager may reach the Loki gateway pod port, nothing new.
yq -o=json 'select(.kind == "NetworkPolicy" and .metadata.name == "allow-runtime")' "$render" | jq -e '
  any(.spec.egress[];
    any(.to[]?;
      .namespaceSelector.matchLabels."kubernetes.io/metadata.name" == "monitoring"
      and .podSelector.matchLabels."app.kubernetes.io/name" == "loki"
      and .podSelector.matchLabels."app.kubernetes.io/component" == "gateway")
    and any(.ports[]?; .port == 8080 and .protocol == "TCP"))
' >/dev/null

# Shipping alerts must not turn on the raw archive.
for conf in master.conf worker.conf; do
  content=$(yq -r "select(.kind == \"ConfigMap\" and .metadata.name == \"wazuh-conf\") | .data[\"$conf\"]" "$render")
  grep -Fq '<logall>no</logall>' <<<"$content"
  grep -Fq '<logall_json>no</logall_json>' <<<"$content"
  if grep -Fq '<logall>yes</logall>' <<<"$content" || grep -Fq '<logall_json>yes</logall_json>' <<<"$content"; then
    echo "$conf enables raw event archiving" >&2
    exit 1
  fi
done

# Cross-namespace delivery: monitoring is deny-by-default for ingress from other
# namespaces, so without this policy the sidecar runs but its pushes are dropped.
policy=$root/platform/monitoring/templates/networkpolicy.yaml
grep -Fq 'name: allow-wazuh-alerts-to-loki-gateway' "$policy"
grep -Fq 'kubernetes.io/metadata.name: wazuh' "$policy"
grep -Fq 'port: 8080' "$policy"
