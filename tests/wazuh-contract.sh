#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
app=$root/platform/wazuh
render=$(mktemp)
trap 'rm -f "$render"' EXIT
kubectl kustomize "$app" >"$render"

test "$(yq -r '.spec.project' "$root/clusters/kepler/apps/wazuh.yaml")" = platform
test "$(yq -r 'select(.metadata.name == "platform") | .spec.namespaceResourceWhitelist[] | select(.group == "traefik.io" and .kind == "ServersTransport") | .kind' "$root/clusters/kepler/apps/projects.yaml")" = ServersTransport
test "$(yq -r '.spec.ignoreDifferences[] | select(.group == "apps" and .kind == "StatefulSet") | .jqPathExpressions[]' "$root/clusters/kepler/apps/wazuh.yaml")" = '.spec.volumeClaimTemplates[]?.status'
grep -Fq 'wazuh.yaml' "$root/clusters/kepler/apps/kustomization.yaml"
grep -Fq 'v4.14.7' "$app/UPSTREAM.md"
grep -Fq '14f78b70ba553db2e3c0b3d1c013bde4d306a911' "$app/UPSTREAM.md"

for workload in wazuh-indexer wazuh-manager-master wazuh-manager-worker wazuh-dashboard; do
  image=$(yq -r "select((.kind == \"Deployment\" or .kind == \"StatefulSet\") and .metadata.name == \"$workload\") | .spec.template.spec.containers[] | select(.name != \"volume-mount-hack\") | .image" "$render")
  [[ $image == *@sha256:* ]]
done
if grep -En 'privileged: true|increase-the-vm-max-map-count' "$render"; then
  echo 'Wazuh rendered a privileged sysctl container' >&2
  exit 1
fi

for tuple in 'wazuh-agent-ingest TCP 1514 31514' 'wazuh-registration TCP 1515 31515' 'wazuh-agent-ingest UDP 5514 30514'; do
  read -r service protocol port node_port <<<"$tuple"
  yq -e "select(.kind == \"Service\" and .metadata.name == \"$service\") | .spec.type == \"NodePort\" and (.spec.ports[] | select(.protocol == \"$protocol\" and .port == $port and .nodePort == $node_port))" "$render" >/dev/null
done

for secret in indexer-certs dashboard-certs wazuh-api-cred wazuh-authd-pass wazuh-cluster-key dashboard-cred indexer-cred; do
  test "$(yq -r "select(.kind == \"ExternalSecret\" and .spec.target.name == \"$secret\") | .spec.secretStoreRef.name" "$render")" = vault-platform
done
test "$(yq -r 'select(.kind == "Ingress") | .spec.rules[0].host' "$render")" = wazuh.homelab.pastelariadev.com
test "$(yq -r 'select(.kind == "Ingress") | .metadata.annotations."traefik.ingress.kubernetes.io/service.serverstransport"' "$render")" = wazuh-dashboard@kubernetescrd
test "$(yq -r 'select(.kind == "ServersTransport" and .metadata.name == "dashboard") | .spec.serverName' "$render")" = dashboard
test "$(yq -r 'select(.kind == "ServersTransport" and .metadata.name == "dashboard") | .spec.rootCAs[0].secret' "$render")" = dashboard-certs
test "$(yq -r 'select(.kind == "ExternalSecret" and .metadata.name == "dashboard-certs") | .spec.target.template.data."ca.crt"' "$render")" = '{{ index . "root-ca.pem" }}'
test "$(yq -r 'select(.kind == "Service" and .metadata.name == "dashboard") | .spec.type' "$render")" = ClusterIP
test "$(yq -r 'select(.kind == "NetworkPolicy" and .metadata.name == "default-deny") | .spec.policyTypes | join(",")' "$render")" = Ingress,Egress
yq -e 'select(.kind == "StatefulSet") | (.spec.volumeClaimTemplates[].spec.accessModes | join(",")) == "ReadWriteOnce" and .spec.volumeClaimTemplates[].spec.storageClassName == "local-path" and .spec.volumeClaimTemplates[].spec.resources.requests.storage' "$render" >/dev/null
python3 "$root/scripts/check-first-wave-resources.py" "$render"
