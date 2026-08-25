#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
app=$root/apps/home-services/immich
render=$(mktemp)
trap 'rm -f "$render"' EXIT

test "$(yq -r '.dependencies[0].repository' "$app/Chart.yaml")" = oci://ghcr.io/immich-app/immich-charts
test "$(yq -r '.dependencies[0].version' "$app/Chart.yaml")" = 0.13.1
test "$(yq -r '.spec.project' "$root/clusters/kepler/apps/immich.yaml")" = home-services
grep -Fq 'immich.yaml' "$root/clusters/kepler/apps/kustomization.yaml"

helm template immich "$app" >"$render"

for component in immich-server immich-machine-learning immich-postgres; do
  image=$(yq -r "select((.kind == \"Deployment\" or .kind == \"StatefulSet\") and .metadata.name == \"$component\") | .spec.template.spec.containers[0].image" "$render")
  [[ $image == *@sha256:* ]]
done
if grep -Ei 'nvidia.com/gpu|amd.com/gpu|runtimeClassName' "$render"; then
  echo 'Immich rendered unavailable GPU configuration' >&2
  exit 1
fi

test "$(yq -r 'select(.kind == "ExternalSecret") | .spec.secretStoreRef.name' "$render")" = vault-home-services
test "$(yq -r 'select(.kind == "ExternalSecret") | .spec.dataFrom[0].extract.key' "$render")" = home-services/immich
test "$(yq -r 'select(.kind == "PersistentVolumeClaim" and .metadata.name == "immich-library") | .spec.storageClassName' "$render")" = nfs-slow
test "$(yq -r 'select(.kind == "PersistentVolumeClaim" and .metadata.name == "immich-postgres") | .spec.storageClassName' "$render")" = local-path
test "$(yq -r 'select(.kind == "Ingress") | .spec.rules[0].host' "$render")" = immich.homelab.pastelariadev.com
test "$(yq -r 'select(.kind == "NetworkPolicy" and .metadata.name == "default-deny") | .spec.policyTypes | join(",")' "$render")" = Ingress,Egress

python3 "$root/scripts/check-first-wave-resources.py" --forbid-gpu "$render"
