#!/usr/bin/env bash
set -euo pipefail

class=homelab-platform-critical

grep -Fq "name: $class" argocd/templates/priorityclass.yaml
grep -Fq 'value: 100000' argocd/templates/priorityclass.yaml
grep -Fq "priorityClassName: $class" argocd/values.yaml
grep -Fq "priorityClassName: $class" platform/traefik/values.yaml
grep -Fq "priorityClassName: $class" platform/keda/values.yaml
! grep -Fq "priorityClassName: $class" platform/vcluster-pastelariadev/values.yaml

for chart in argocd platform/traefik platform/keda; do
  manifest=$(mktemp)
  if ! helm template hardening "$chart" > "$manifest" 2>/dev/null; then
    helm dependency build "$chart" >/dev/null
    helm template hardening "$chart" > "$manifest"
  fi
  test "$(yq ea -r 'select(.kind == "Deployment" or .kind == "StatefulSet") | .spec.template.spec.priorityClassName' "$manifest" | sort -u)" = "$class"
  rm -f "$manifest"
done
