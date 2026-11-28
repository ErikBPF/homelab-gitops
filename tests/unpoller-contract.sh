#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
dir=$root/platform/unpoller
app=$root/clusters/kepler/apps/unpoller.yaml

# App registration: same platform project and shape as the other platform apps.
grep -Fxq '  - unpoller.yaml' "$root/clusters/kepler/apps/kustomization.yaml"
test "$(yq -r '.spec.project' "$app")" = platform
test "$(yq -r '.spec.source.path' "$app")" = platform/unpoller
test "$(yq -r '.spec.destination.namespace' "$app")" = monitoring
test "$(yq -r '.spec.source.targetRevision' "$app")" = main

# The API key comes from OpenBao through the sanctioned platform store.
es=$dir/externalsecret.yaml
test "$(yq -r '.spec.secretStoreRef.kind' "$es")" = ClusterSecretStore
test "$(yq -r '.spec.secretStoreRef.name' "$es")" = vault-platform
test "$(yq -r '.spec.target.name' "$es")" = unpoller
test "$(yq -r '.spec.data[0].secretKey' "$es")" = api_key
test "$(yq -r '.spec.data[0].remoteRef.key' "$es")" = platform/unifi
test "$(yq -r '.spec.data[0].remoteRef.property' "$es")" = api_key

dep=$dir/deployment.yaml
test "$(yq 'select(.kind == "Deployment") | .spec.replicas' "$dep")" = 1
test "$(yq 'select(.kind == "Deployment") | .spec.selector.matchLabels.app' "$dep")" = unpoller

# Image is pinned by tag plus digest through the Harbor mirror.
image=$(yq 'select(.kind == "Deployment") | .spec.template.spec.containers[0].image' "$dep")
test "$image" = "harbor.homelab.pastelariadev.com/ghcr/unpoller/unpoller:v2.34.0@sha256:73423ddfe304e866b2cda2d3ecf8e68c15317c93a22945ad7d941d3ca3a6d022"

env_value() {
  yq "select(.kind == \"Deployment\") | .spec.template.spec.containers[0].env[] | select(.name == \"$1\") | $2" "$dep"
}
# Metrics-only: Influx is off, Prometheus is on with the `unifi_` namespace.
test "$(env_value UP_INFLUXDB_DISABLE .value)" = true
test "$(env_value UP_PROMETHEUS_NAMESPACE .value)" = unifi
test "$(env_value UP_PROMETHEUS_HTTP_LISTEN .value)" = 0.0.0.0:9130
test "$(env_value UP_UNIFI_DEFAULT_URL .value)" = https://192.168.10.1
test "$(env_value UP_UNIFI_DEFAULT_VERIFY_SSL .value)" = false
test "$(env_value UP_UNIFI_DEFAULT_API_KEY .valueFrom.secretKeyRef.name)" = unpoller
test "$(env_value UP_UNIFI_DEFAULT_API_KEY .valueFrom.secretKeyRef.key)" = api_key
test -z "$(env_value UP_UNIFI_DEFAULT_API_KEY '.value // ""')"

# No secret literal in the tree: the key is only ever referenced by name.
test -z "$(grep -rnE '(api_key|API_KEY|apiKey)[[:space:]]*[:=][[:space:]]*"?[A-Za-z0-9_-]{16,}' "$dir" || true)"
test -z "$(grep -rn 'value: *"\?[A-Za-z0-9_-]\{32,\}' "$dir" || true)"

# Egress: cluster DNS plus the controller API only. No wildcard, no other ports.
np=$dir/networkpolicy.yaml
test "$(yq -r '.spec.policyTypes | join(",")' "$np")" = Egress
test "$(yq -r '.spec.egress | length' "$np")" = 2
test "$(yq -r '.spec.egress[1].to[0].ipBlock.cidr' "$np")" = 192.168.10.1/32
test "$(yq -r '.spec.egress[1].ports[0].port' "$np")" = 443
test "$(yq -r '.spec.egress[1].ports[0].protocol' "$np")" = TCP
test "$(yq -r '.spec.egress[0].ports | map(.port) | join(",")' "$np")" = 53,53
test -z "$(grep -F '0.0.0.0/0' "$np" || true)"

# Prometheus scrapes this pod: the wrapper selects `release: monitoring` in the
# `monitoring` namespace, and the ServiceMonitor must carry that label.
sm=$dir/servicemonitor.yaml
test "$(yq -r '.metadata.labels.release' "$sm")" = monitoring
test "$(yq -r '.spec.selector.matchLabels.app' "$sm")" = unpoller
test "$(yq -r '.spec.endpoints[0].port' "$sm")" = http
test "$(yq -r '.spec.endpoints[0].path' "$sm")" = /metrics
test "$(yq 'select(.kind == "Service") | .spec.ports[0].targetPort' "$dir/deployment.yaml")" = http
