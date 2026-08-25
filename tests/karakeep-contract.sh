#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
app=$root/apps/home-services/karakeep
render=$(mktemp)
trap 'rm -f "$render"' EXIT
kubectl kustomize "$app" >"$render"

test "$(yq -r '.spec.project' "$root/clusters/kepler/apps/karakeep.yaml")" = home-services
test "$(yq -r '.spec.source.path' "$root/clusters/kepler/apps/karakeep.yaml")" = apps/home-services/karakeep
grep -Fq 'karakeep.yaml' "$root/clusters/kepler/apps/kustomization.yaml"
grep -Fq 'v0.33.2' "$app/UPSTREAM.md"
grep -Fq 'd472a3a1c428bad8ce2ddc0822fb5b327e9465d4' "$app/UPSTREAM.md"

for name in web chrome meilisearch; do
  image=$(yq -r "select(.kind == \"Deployment\" and .metadata.name == \"$name\") | .spec.template.spec.containers[0].image" "$render")
  [[ $image == *@sha256:* ]]
  yq -e "select(.kind == \"Deployment\" and .metadata.name == \"$name\") |
    .spec.template.spec.containers[0].readinessProbe and
    .spec.template.spec.containers[0].livenessProbe and
    .spec.template.spec.containers[0].resources.requests.cpu and
    .spec.template.spec.containers[0].resources.limits.memory" "$render" >/dev/null
done

test "$(yq -r 'select(.kind == "ExternalSecret") | .spec.secretStoreRef.name' "$render")" = vault-home-services
test "$(yq -r 'select(.kind == "ExternalSecret") | .spec.dataFrom[0].extract.key' "$render")" = home-services/karakeep
test "$(yq -r 'select(.kind == "Ingress") | .spec.rules[0].host' "$render")" = bookmarks.homelab.pastelariadev.com
test "$(yq -r 'select(.kind == "PersistentVolumeClaim") | .spec.storageClassName' "$render" | sed '/^---$/d' | sort -u)" = $'local-path\nnfs-fast'
test "$(yq -r 'select(.kind == "NetworkPolicy" and .metadata.name == "default-deny") | .spec.policyTypes | join(",")' "$render")" = Ingress,Egress

python3 "$root/scripts/check-first-wave-resources.py" "$render"
