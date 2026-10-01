#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

for app in monitoring loki scrutiny; do
  manifest=$root/clusters/kepler/apps/$app.yaml
  test "$(yq -r '.spec.project' "$manifest")" = platform
  test "$(yq -r '.spec.destination.namespace' "$manifest")" = monitoring
  grep -Fq "$app.yaml" "$root/clusters/kepler/apps/kustomization.yaml"
done

test "$(yq -r '.kube-prometheus-stack.kubeStateMetrics.enabled' "$root/platform/monitoring/values.yaml")" = false
test "$(yq -r '.kube-prometheus-stack.nodeExporter.enabled' "$root/platform/monitoring/values.yaml")" = false
test "$(yq -r '.kube-prometheus-stack.prometheusOperator.tls.enabled' "$root/platform/monitoring/values.yaml")" = false
test "$(yq -r '.kube-prometheus-stack.prometheus.prometheusSpec.resources.requests.cpu' "$root/platform/monitoring/values.yaml")" = 100m
test "$(yq -r '.kube-prometheus-stack.defaultRules.disabled.PrometheusNotConnectedToAlertmanagers' "$root/platform/monitoring/values.yaml")" = true
test "$(yq -r '.kube-prometheus-stack.defaultRules.disabled.KubeletDown // false' "$root/platform/monitoring/values.yaml")" = false
test "$(yq -r '.kube-prometheus-stack.kubelet.serviceMonitor.relabelings[] | select(.targetLabel == "cluster") | .replacement' "$root/platform/monitoring/values.yaml")" = homelab
! grep -Fq 'legacy-monitoring-federation' "$root/platform/monitoring/values.yaml"
test "$(yq -r '.kube-prometheus-stack.prometheus.prometheusSpec.additionalScrapeConfigs // "absent"' "$root/platform/monitoring/values.yaml")" = absent
! grep -Fq 'wazuh_unifi_last_event_seconds' "$root/platform/monitoring/files/alerting/rules.yaml"
yq -e '.deleteRules[] | select(.orgId == 1 and .uid == "security-control-unifi-siem-silent")' "$root/platform/monitoring/files/alerting/rules.yaml" >/dev/null
yq -e '.deleteRules[] | select(.orgId == 1 and .uid == "litellm-semantic-readiness-failed")' "$root/platform/monitoring/files/alerting/recovery-rules.yaml" >/dev/null
! yq -e '.groups[].rules[] | select(.uid == "litellm-semantic-readiness-failed")' "$root/platform/monitoring/files/alerting/recovery-rules.yaml" >/dev/null 2>&1
if yq -r '.groups[].rules[] | select(.labels.category == "security") | .annotations.summary' \
  "$root/platform/monitoring/files/alerting/rules.yaml" | \
  grep -Eq '\$labels\.(source_ip|ssh_user|auth_method|fingerprint)|\$values\.'; then
  echo "security alert summaries expose event values" >&2
  exit 1
fi
for component in prometheus grafana; do
  test "$(yq -r ".kube-prometheus-stack.$component.ingress.tls[0].secretName" "$root/platform/monitoring/values.yaml")" = monitoring-ingress-tls
done
test "$(yq -r '.loki.deploymentMode' "$root/platform/loki/values.yaml")" = SingleBinary
test "$(yq -r '.loki.singleBinary.replicas' "$root/platform/loki/values.yaml")" = 1
for mode in backend read write; do
  test "$(yq -r ".loki.$mode.replicas" "$root/platform/loki/values.yaml")" = 0
done
for app in monitoring loki; do
  render=$(mktemp)
  helm template "$app" "$root/platform/$app" >"$render"
  python3 "$root/scripts/check-first-wave-resources.py" "$render"
  rm -f "$render"
done

render=$(mktemp)
helm template monitoring "$root/platform/monitoring" >"$render"
! yq -e 'select(.kind == "PrometheusRule") | .spec.groups[].rules[]? | select(.alert == "PrometheusNotConnectedToAlertmanagers")' "$render" >/dev/null 2>&1
yq -e 'select(.kind == "PrometheusRule") | .spec.groups[].rules[]? | select(.alert == "KubeletDown")' "$render" >/dev/null
test "$(yq -r 'select(.kind == "ServiceMonitor" and .metadata.name == "monitoring-kube-prometheus-kubelet") | .spec.endpoints[] | select(.path == null) | .relabelings[] | select(.targetLabel == "cluster") | .replacement' "$render")" = homelab
test "$(yq -r 'select(.kind == "ServiceMonitor" and .metadata.name == "monitoring-kube-prometheus-kubelet") | .spec.endpoints[] | select(.path == null) | .relabelings[] | select(.targetLabel == "cluster") | .action' "$render")" = replace
test "$(yq -r 'select(.kind == "ServiceMonitor" and .metadata.name == "monitoring-kube-prometheus-kubelet") | .spec.endpoints[] | select(.path == null) | .relabelings[] | select(.targetLabel == "metrics_path" and .action == "replace") | .sourceLabels[]' "$render")" = __metrics_path__
rm -f "$render"

for app in scrutiny; do
  render=$(mktemp)
  kubectl kustomize "$root/platform/$app" >"$render"
  yq -e 'select(.kind == "Deployment") | .spec.template.spec.containers[0].image | contains("@sha256:")' "$render" >/dev/null
  yq -e 'select(.kind == "Deployment") | .spec.template.spec.containers[0].readinessProbe and .spec.template.spec.containers[0].livenessProbe' "$render" >/dev/null
  yq -e 'select(.kind == "PersistentVolumeClaim") | .spec.storageClassName and .spec.resources.requests.storage' "$render" >/dev/null
  yq -e 'select(.kind == "Ingress") | .spec.rules[0].host' "$render" >/dev/null
  python3 "$root/scripts/check-first-wave-resources.py" "$render"
  rm -f "$render"
done

grep -Fq 'vault-platform' "$root/platform/monitoring/templates/externalsecret.yaml"
test "$(yq -r 'select(.metadata.name == "default-deny") | .spec.policyTypes | join(",")' "$root/platform/monitoring/templates/networkpolicy.yaml")" = Ingress,Egress
test "$(yq -r '.kube-prometheus-stack.grafana.sidecar.alerts.enabled' "$root/platform/monitoring/values.yaml")" = true
for file in contactpoints policies rules storage-rules templates; do
  test -s "$root/platform/monitoring/files/alerting/$file.yaml"
done
grep -Fq 'grafana_alert: "1"' "$root/platform/monitoring/templates/grafana-alerting.yaml"
render=$(mktemp)
helm template monitoring "$root/platform/monitoring" >"$render"
for kind in Issuer Certificate; do
  yq -e "select(.kind == \"$kind\")" "$render" >/dev/null
done
test "$(yq -r 'select(.kind == "Certificate") | .spec.secretName' "$render")" = monitoring-ingress-tls
rm -f "$render"
test -x "$root/scripts/bootstrap-monitoring-dns01-token.sh"
grep -Fq 'IFS= read -r api_token' "$root/scripts/bootstrap-monitoring-dns01-token.sh"
! grep -Fq 'http://hermes-argus:8644' "$root/platform/monitoring/files/alerting/contactpoints.yaml"
for uid in argus-webhook-incidents argus-webhook-deploys; do
  yq -e ".deleteContactPoints[] | select(.orgId == 1 and .uid == \"$uid\")" "$root/platform/monitoring/files/alerting/contactpoints.yaml" >/dev/null
done
container_expr=$(yq -r '.groups[].rules[] | select(.uid == "container-critical-down") | .data[] | select(.refId == "A") | .model.expr' "$root/platform/monitoring/files/alerting/rules.yaml")
grep -Fq 'and on() (max(up{instance="kepler",job="integrations/unix"}) == 1)' <<<"$container_expr"
test "$(yq -r '.groups[].rules[] | select(.uid == "k8s-statefulset-replicas-mismatch") | .for' "$root/platform/monitoring/files/alerting/rules.yaml")" = 15m
statefulset_expr=$(yq -r '.groups[].rules[] | select(.uid == "k8s-statefulset-replicas-mismatch") | .data[] | select(.refId == "A") | .model.expr' "$root/platform/monitoring/files/alerting/rules.yaml")
grep -Fq 'kube_statefulset_status_replicas_ready' <<<"$statefulset_expr"
grep -Fq 'kube_statefulset_replicas' <<<"$statefulset_expr"

host_absence_expr=$(yq -r '.groups[].rules[] | select(.uid == "host-metrics-absent") | .data[] | select(.refId == "A") | .model.expr' "$root/platform/monitoring/files/alerting/rules.yaml")
grep -Fq 'instance="apollo"' <<<"$host_absence_expr"
test "$(grep -c 'uid: host-systemd-unit-failed' "$root/platform/monitoring/files/alerting/rules.yaml")" -eq 1

alert_files=(
  "$root/platform/monitoring/files/alerting/rules.yaml"
  "$root/platform/monitoring/files/alerting/buzz-rules.yaml"
  "$root/platform/monitoring/files/alerting/recovery-rules.yaml"
  "$root/platform/monitoring/files/alerting/storage-rules.yaml"
)
unexpected_error_rules=$(yq -r '.groups[].rules[] | select(.uid != "datasource-prometheus-health" and .uid != "datasource-loki-health" and .execErrState != "KeepLast") | .uid' "${alert_files[@]}")
test -z "$unexpected_error_rules"
for spec in datasource-prometheus-health:prometheus datasource-loki-health:loki; do
  uid=${spec%%:*}
  datasource=${spec#*:}
  test "$(yq -r ".groups[].rules[] | select(.uid == \"$uid\") | .execErrState" "${alert_files[@]}")" = Error
  test "$(yq -r ".groups[].rules[] | select(.uid == \"$uid\") | .for" "${alert_files[@]}")" = 2m
  test "$(yq -r ".groups[].rules[] | select(.uid == \"$uid\") | .data[] | select(.refId == \"A\") | .datasourceUid" "${alert_files[@]}")" = "$datasource"
done
test "$(yq -r '.policies[0].group_by | join(",")' "$root/platform/monitoring/files/alerting/policies.yaml")" = grafana_folder,alertname,datasource_uid
