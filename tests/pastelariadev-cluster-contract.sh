#!/usr/bin/env bash
set -euo pipefail

chart=platform/vcluster-pastelariadev/Chart.yaml
values=platform/vcluster-pastelariadev/values.yaml
application=clusters/kepler/apps/vcluster-pastelariadev.yaml

grep -Fq 'version: 0.36.1' "$chart"
grep -Fq 'podSecurityStandard: restricted' "$values"
grep -Fq 'useSecretsForSATokens: true' "$values"
grep -Fq 'harbor.homelab.pastelariadev.com/dockerhub/library/busybox:1.37.0' "$values"
grep -Fq 'context: pastelariadev' "$values"
grep -Fq 'name: vcluster-pastelariadev' "$application"
grep -Fq 'namespace: vcluster-pastelariadev' "$application"
grep -Fq 'ctx := "homelab"' justfile
grep -Fq 'connect pastelariadev' justfile
grep -Fq 'diagnose-pastelariadev:' justfile
grep -Fq 'kubectl wait --for=condition=complete job/harbor-pull-smoke' justfile

helm repo add loft https://charts.loft.sh >/dev/null 2>&1 || true
helm dependency build platform/vcluster-pastelariadev >/dev/null
manifest=$(mktemp)
trap 'rm -f "$manifest"' EXIT
helm template pastelariadev platform/vcluster-pastelariadev \
  --namespace vcluster-pastelariadev > "$manifest"

test "$(yq ea -r 'select(.kind == "ResourceQuota") | .spec.hard."requests.cpu"' "$manifest")" = 1500m
test "$(yq ea -r 'select(.kind == "LimitRange") | .spec.limits[0].defaultRequest.cpu' "$manifest")" = 100m
test "$(yq ea -r 'select(.kind == "NetworkPolicy") | .metadata.name' "$manifest" | wc -l)" -ge 2
yq ea -r 'select(.kind == "Secret" and .metadata.name == "vc-config-pastelariadev") | .data."config.yaml"' "$manifest" \
  | base64 --decode \
  | grep -Fq 'kind: Job'
