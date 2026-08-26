#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

for app in monitoring loki uptime-kuma healthchecks scrutiny; do
  manifest=$root/clusters/kepler/apps/$app.yaml
  test "$(yq -r '.spec.project' "$manifest")" = platform
  test "$(yq -r '.spec.destination.namespace' "$manifest")" = monitoring
  grep -Fq "$app.yaml" "$root/clusters/kepler/apps/kustomization.yaml"
done

test "$(yq -r '.dependencies[0].version' "$root/platform/monitoring/Chart.yaml")" = 80.14.4
test "$(yq -r '.kube-prometheus-stack.kubeStateMetrics.enabled' "$root/platform/monitoring/values.yaml")" = false
test "$(yq -r '.kube-prometheus-stack.nodeExporter.enabled' "$root/platform/monitoring/values.yaml")" = false
test "$(yq -r '.kube-prometheus-stack.prometheusOperator.tls.enabled' "$root/platform/monitoring/values.yaml")" = false
test "$(yq -r '.kube-prometheus-stack.prometheus.prometheusSpec.resources.requests.cpu' "$root/platform/monitoring/values.yaml")" = 100m
federation='.kube-prometheus-stack.prometheus.prometheusSpec.additionalScrapeConfigs[] | select(.job_name == "legacy-monitoring-federation")'
test "$(yq -r "$federation | .metrics_path" "$root/platform/monitoring/values.yaml")" = /federate
test "$(yq -r "$federation | .honor_labels" "$root/platform/monitoring/values.yaml")" = true
test "$(yq -r "$federation | .static_configs[0].targets[0]" "$root/platform/monitoring/values.yaml")" = 100.76.140.121:9090
grep -Fq '{job="integrations/unix",instance="kepler"}' "$root/platform/monitoring/values.yaml"
grep -Fq '{job=~"adguard|buzz-relay|cloudflared|litellm|llamacpp|node-vanguard|node-voyager|nvidia-gpu|postgres-discovery|postgres-kepler"}' "$root/platform/monitoring/values.yaml"
test "$(yq -r '.dependencies[0].version' "$root/platform/loki/Chart.yaml")" = 6.51.0
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

for app in uptime-kuma healthchecks scrutiny; do
  render=$(mktemp)
  kubectl kustomize "$root/platform/$app" >"$render"
  yq -e 'select(.kind == "Deployment") | .spec.template.spec.containers[0].image | contains("@sha256:")' "$render" >/dev/null
  yq -e 'select(.kind == "Deployment") | .spec.template.spec.containers[0].readinessProbe and .spec.template.spec.containers[0].livenessProbe' "$render" >/dev/null
  yq -e 'select(.kind == "PersistentVolumeClaim") | .spec.storageClassName and .spec.resources.requests.storage' "$render" >/dev/null
  yq -e 'select(.kind == "Ingress") | .spec.rules[0].host' "$render" >/dev/null
  python3 "$root/scripts/check-first-wave-resources.py" "$render"
  rm -f "$render"
done

render=$(mktemp)
kubectl kustomize "$root/platform/healthchecks" >"$render"
for probe in readinessProbe livenessProbe; do
  test "$(yq -r "select(.kind == \"Deployment\") | .spec.template.spec.containers[0].$probe.httpGet.httpHeaders[0].value" "$render")" = healthchecks.homelab.pastelariadev.com
done
rm -f "$render"

grep -Fq 'vault-platform' "$root/platform/monitoring/templates/externalsecret.yaml"
test "$(yq -r 'select(.metadata.name == "default-deny") | .spec.policyTypes | join(",")' "$root/platform/monitoring/templates/networkpolicy.yaml")" = Ingress,Egress
test "$(yq -r '.kube-prometheus-stack.grafana.sidecar.alerts.enabled' "$root/platform/monitoring/values.yaml")" = true
for file in contactpoints policies rules storage-rules templates; do
  test -s "$root/platform/monitoring/files/alerting/$file.yaml"
done
grep -Fq 'grafana_alert: "1"' "$root/platform/monitoring/templates/grafana-alerting.yaml"
! grep -Fq 'http://hermes-argus:8644' "$root/platform/monitoring/files/alerting/contactpoints.yaml"
container_expr=$(yq -r '.groups[].rules[] | select(.uid == "container-critical-down") | .data[] | select(.refId == "A") | .model.expr' "$root/platform/monitoring/files/alerting/rules.yaml")
grep -Fq 'and on() (max(up{instance="kepler",job="integrations/unix"}) == 1)' <<<"$container_expr"
test "$(yq -r '.groups[].rules[] | select(.uid == "k8s-statefulset-replicas-mismatch") | .for' "$root/platform/monitoring/files/alerting/rules.yaml")" = 15m
statefulset_expr=$(yq -r '.groups[].rules[] | select(.uid == "k8s-statefulset-replicas-mismatch") | .data[] | select(.refId == "A") | .model.expr' "$root/platform/monitoring/files/alerting/rules.yaml")
grep -Fq 'kube_statefulset_status_replicas_ready' <<<"$statefulset_expr"
grep -Fq 'kube_statefulset_replicas' <<<"$statefulset_expr"
