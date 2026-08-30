#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
app=$root/platform/authentik
render=$(mktemp)
trap 'rm -f "$render"' EXIT

test "$(yq -r '.dependencies[0].version' "$app/Chart.yaml")" = 2026.5.6
test "$(yq -r '.dependencies[0].repository' "$app/Chart.yaml")" = https://charts.goauthentik.io
grep -Fq 'authentik.yaml' "$root/clusters/kepler/apps/kustomization.yaml"
test "$(yq -r '.spec.project' "$root/clusters/kepler/apps/authentik.yaml")" = platform
test "$(yq -r 'select(.kind == "Namespace" and .metadata.name == "authentik") | .metadata.name' "$root/clusters/kepler/apps/namespaces.yaml")" = authentik

helm repo add authentik https://charts.goauthentik.io >/dev/null 2>&1 || true
helm dependency build "$app" >/dev/null
helm template authentik "$app" --namespace authentik >"$render"

for workload in authentik-server authentik-worker authentik-postgresql; do
  image=$(yq -r "select((.kind == \"Deployment\" or .kind == \"StatefulSet\") and .metadata.name == \"$workload\") | .spec.template.spec.containers[0].image" "$render")
  [[ $image == *@sha256:* ]]
done

if yq -e 'select(.kind == "ClusterRole" or .kind == "ClusterRoleBinding")' "$render" >/dev/null 2>&1; then
  echo 'Authentik rendered cluster-scoped outpost RBAC' >&2
  exit 1
fi

for workload in authentik-server authentik-worker; do
  test "$(yq -r "select(.kind == \"Deployment\" and .metadata.name == \"$workload\") | .spec.template.spec.securityContext.runAsNonRoot" "$render")" = true
  test "$(yq -r "select(.kind == \"Deployment\" and .metadata.name == \"$workload\") | .spec.template.spec.containers[0].securityContext.allowPrivilegeEscalation" "$render")" = false
done

test "$(yq -r 'select(.kind == "ExternalSecret") | .spec.secretStoreRef.name' "$render")" = vault-platform
test "$(yq -r 'select(.kind == "ExternalSecret") | .spec.dataFrom[0].extract.key' "$render")" = platform/authentik
test "$(yq -r 'select(.kind == "Deployment" and .metadata.name == "authentik-server") | .spec.template.spec.containers[0].env[] | select(.name == "AUTHENTIK_POSTGRESQL__HOST") | .value' "$render")" = authentik-postgresql
test "$(yq -r 'select(.kind == "Ingress") | .spec.rules[0].host' "$render")" = authentik.homelab.pastelariadev.com
if yq -e 'select(.kind == "Middleware")' "$render" >/dev/null 2>&1; then
  echo 'Authentik rendered a source-IP middleware behind a source-hiding L4 proxy' >&2
  exit 1
fi
! yq -e 'select(.kind == "Ingress") | .metadata.annotations."traefik.ingress.kubernetes.io/router.middlewares"' "$render" >/dev/null 2>&1
test "$(yq -r 'select(.kind == "NetworkPolicy" and .metadata.name == "default-deny") | .spec.policyTypes | join(",")' "$render")" = Ingress,Egress
test "$(yq -r 'select(.kind == "NetworkPolicy" and .metadata.name == "authentik-server-ingress") | .spec.podSelector.matchLabels."app.kubernetes.io/component"' "$render")" = server
test "$(yq -r 'select(.kind == "ServiceMonitor") | .metadata.name' "$render" | grep -v '^---$' | sort -u)" = $'authentik-server\nauthentik-worker'

if yq -e 'select(.kind == "Service" and (.spec.type == "NodePort" or .spec.type == "LoadBalancer"))' "$render" >/dev/null 2>&1; then
  echo 'Authentik rendered a public service type' >&2
  exit 1
fi

for workload in authentik-server authentik-worker authentik-postgresql; do
  yq -e "select((.kind == \"Deployment\" or .kind == \"StatefulSet\") and .metadata.name == \"$workload\") | .spec.template.spec.containers[0].resources.requests.cpu" "$render" >/dev/null
done

test "$(yq -r '.authentik.postgresql.enabled' "$app/values.yaml")" = false
grep -Fq 'name: authentik-bootstrap' "$app/values.yaml"
test "$(yq -r 'select(.kind == "StatefulSet" and .metadata.name == "authentik-postgresql") | .spec.template.spec.containers[0].env[] | select(.name == "PGDATA") | .value' "$render")" = /var/lib/postgresql/data/pgdata
