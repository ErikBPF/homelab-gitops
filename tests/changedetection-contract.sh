#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
app=$root/apps/home-services/changedetection

test "$(yq -r '.spec.project' "$root/clusters/kepler/apps/changedetection.yaml")" = home-services
test "$(yq -r '.spec.source.path' "$root/clusters/kepler/apps/changedetection.yaml")" = apps/home-services/changedetection
grep -Fq 'changedetection.yaml' "$root/clusters/kepler/apps/kustomization.yaml"

image=$(yq -r '.spec.template.spec.containers[0].image' "$app/deployment.yaml")
[[ $image == ghcr.io/dgtlmoon/changedetection.io:0.55.7@sha256:* ]]
test "$(yq -r '.spec.storageClassName' "$app/pvc.yaml")" = nfs-fast
test "$(yq -r '.spec.resources.requests.storage' "$app/pvc.yaml")" = 5Gi
test "$(yq -r '.spec.rules[0].host' "$app/ingress.yaml")" = changedetection.homelab.pastelariadev.com
yq -e '.spec.template.spec.containers[0].readinessProbe.httpGet.path == "/" and .spec.template.spec.containers[0].livenessProbe.httpGet.path == "/"' "$app/deployment.yaml" >/dev/null
test "$(yq -r 'select(.metadata.name == "default-deny") | .spec.policyTypes | join(",")' "$app/networkpolicy.yaml")" = Ingress,Egress

python3 "$root/scripts/check-first-wave-resources.py" "$app"/*.yaml
