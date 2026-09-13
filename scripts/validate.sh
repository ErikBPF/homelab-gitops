#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"

helm repo add authentik https://charts.goauthentik.io >/dev/null 2>&1 || true

while IFS= read -r chart; do
  dir=$(dirname "$chart")
  helm dependency build "$dir" >/dev/null
  helm template "$(basename "$dir")" "$dir" |
    kubeconform -strict -summary -ignore-missing-schemas
done < <(find platform argocd apps -name Chart.yaml | sort)

while IFS= read -r kustomization; do
  kubectl kustomize "$(dirname "$kustomization")" |
    kubeconform -strict -summary -ignore-missing-schemas
done < <(find platform apps -name kustomization.yaml | sort)

mapfile -t manifests < <(
  find apps clusters bootstrap -name '*.yaml' \
    ! -name Chart.yaml ! -name values.yaml ! -name patches.yaml \
    ! -path '*/templates/*' | sort
)
kubeconform -strict -summary -ignore-missing-schemas "${manifests[@]}"

for contract in \
  tests/apollo-launch.py \
  tests/manual-sync-containment.sh \
  tests/operational-context.sh \
  tests/first-wave-image-pins.py \
  tests/harbor-runtime-images.py \
  tests/path-boundary-contract.sh \
  tests/resource-contract.sh \
  tests/platform-prerequisites-contract.sh \
  tests/lane-secretstore-contract.sh \
  tests/authentik-contract.sh \
  tests/authentik-backup.py \
  tests/test_authentik_restore_drill.py \
  tests/changedetection-contract.sh \
  tests/karakeep-contract.sh \
  tests/immich-contract.sh \
  tests/langfuse-contract.sh \
  tests/monitoring-contract.sh \
  tests/monitoring-promql.py \
  tests/grafana-alert-template_test.go \
  tests/grafana-alert-status.sh \
  tests/wazuh-contract.sh \
  tests/runtime-secret-bootstrap.sh \
  tests/grafana-admin-rotation.sh \
  tests/airflow-contract.sh \
  tests/cognee-contract.sh \
  tests/test_cognee_reader_provision.py \
  tests/alloy-resilience.sh \
  tests/core-scheduling.sh \
  tests/demo-keda-ownership.sh \
  tests/repository-transport.sh; do
  case "$contract" in
    *.py) python3 "$contract" ;;
    *.go) go test "$contract" ;;
    *) bash "$contract" ;;
  esac
done
