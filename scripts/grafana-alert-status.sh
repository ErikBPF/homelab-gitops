#!/usr/bin/env bash
set -euo pipefail

context=${KUBE_CONTEXT:-homelab}
namespace=${GRAFANA_NAMESPACE:-monitoring}
secret=${GRAFANA_SECRET:-monitoring-secrets}
url=${GRAFANA_URL:-https://grafana.homelab.pastelariadev.com}

user=$(kubectl --context "$context" -n "$namespace" get secret "$secret" \
  -o jsonpath='{.data.GRAFANA_ADMIN_USER}' | base64 -d)
password=$(kubectl --context "$context" -n "$namespace" get secret "$secret" \
  -o jsonpath='{.data.GRAFANA_ADMIN_PASSWORD}' | base64 -d)
test -n "$user" && test -n "$password"

alerts=$(curl --config <(printf 'user = "%s:%s"\n' "$user" "$password") \
  --fail --silent --show-error \
  "$url/api/alertmanager/grafana/api/v2/alerts?active=true&silenced=false&inhibited=false")

jq -r '
  "active=\(length)",
  (.[] | [
    (.status.state // "active"),
    (.labels.severity // "unknown"),
    (.labels.alertname // "unnamed"),
    (.labels.host // .labels.instance // "-"),
    (.labels.name // .labels.unit // .labels.application // .labels.app // "-"),
    (.labels.mountpoint // "-")
  ] | @tsv)
' <<<"$alerts"
