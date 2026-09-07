#!/usr/bin/env bash
set -euo pipefail

context=${KUBE_CONTEXT:-homelab}
namespace=${GRAFANA_NAMESPACE:-monitoring}
secret=${GRAFANA_SECRET:-monitoring-secrets}
url=${GRAFANA_URL:-https://grafana.homelab.pastelariadev.com}

user=$(kubectl --context "$context" --request-timeout=15s -n "$namespace" get secret "$secret" \
  -o jsonpath='{.data.GRAFANA_ADMIN_USER}' | base64 -d)
password=$(kubectl --context "$context" --request-timeout=15s -n "$namespace" get secret "$secret" \
  -o jsonpath='{.data.GRAFANA_ADMIN_PASSWORD}' | base64 -d)
test -n "$user" && test -n "$password"

authorization=$(printf '%s:%s' "$user" "$password" | base64 | tr -d '\n')
alerts=$(curl --config <(printf 'header = "Authorization: Basic %s"\n' "$authorization") \
  --connect-timeout 5 --max-time 20 --fail --silent --show-error \
  "$url/api/alertmanager/grafana/api/v2/alerts?active=true&silenced=false&inhibited=false")

jq -er '
  def valid:
    type == "object" and
    (.labels | type == "object" and all(.[]; type == "string")) and
    (.status == null or (.status | type == "object" and (.state | type == "string"))) and
    (.startsAt == null or (.startsAt | type == "string" and
      test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?Z$")));
  if type != "array" or any(.[]; valid | not)
  then error("invalid Grafana alert response") else . end |
  "active=\(length)",
  (["state", "severity", "alert", "target", "component", "namespace", "mount", "since_utc", "age_minutes"] | @tsv),
  (.[] | [
    (.status.state // "active"),
    (.labels.severity // "unknown"),
    (.labels.alertname // "unnamed"),
    (.labels.host // .labels.instance // .labels.node // .labels.cluster // "-"),
    (.labels.name // .labels.unit // .labels.application // .labels.app // .labels.backup // .labels.pod // .labels.job_name // .labels.deployment // .labels.statefulset // .labels.persistentvolumeclaim // .labels.horizontalpodautoscaler // "-"),
    (.labels.namespace // "-"),
    (.labels.mountpoint // "-"),
    (.startsAt // "-"),
    (try (.startsAt | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601 |
      [(now - .) / 60 | floor, 0] | max) catch "-")
  ] | @tsv)
' <<<"$alerts"
