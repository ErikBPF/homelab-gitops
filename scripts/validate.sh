#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"

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
  tests/manual-sync-containment.sh \
  tests/operational-context.sh \
  tests/first-wave-image-pins.py \
  tests/path-boundary-contract.sh \
  tests/resource-contract.sh \
  tests/platform-prerequisites-contract.sh \
  tests/lane-secretstore-contract.sh \
  tests/changedetection-contract.sh \
  tests/karakeep-contract.sh \
  tests/immich-contract.sh \
  tests/langfuse-contract.sh \
  tests/monitoring-contract.sh \
  tests/wazuh-contract.sh \
  tests/runtime-secret-bootstrap.sh \
  tests/grafana-admin-rotation.sh \
  tests/airflow-contract.sh \
  tests/alloy-resilience.sh \
  tests/core-scheduling.sh \
  tests/demo-keda-ownership.sh \
  tests/repository-transport.sh; do
  case "$contract" in
    *.py) python3 "$contract" ;;
    *) bash "$contract" ;;
  esac
done
