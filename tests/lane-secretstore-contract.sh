#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
manifest=$root/platform/external-secrets/templates/clustersecretstore-vault.yaml

for lane in platform homelab home-services; do
  secret=vault-approle-$lane
  test "$(yq -rN "select(.metadata.name == \"vault-$lane\") | .spec.provider.vault.auth.appRole.roleRef | [.name, .key, .namespace] | join(\",\")" "$manifest" | sed '/^$/d')" = "$secret,role_id,external-secrets"
  test "$(yq -rN "select(.metadata.name == \"vault-$lane\") | .spec.provider.vault.auth.appRole.secretRef | [.name, .key, .namespace] | join(\",\")" "$manifest" | sed '/^$/d')" = "$secret,secret_id,external-secrets"
done

test "$(yq -rN 'select(.metadata.name == "vault-platform") | .spec.conditions[0].namespaceRegexes | join(",")' "$manifest" | sed '/^$/d')" = '^(argocd|authentik|cert-manager|clickhouse-operator|external-secrets|keda|kube-system|monitoring|observability|traefik|wazuh)$'
test "$(yq -rN 'select(.metadata.name == "vault-homelab") | .spec.conditions[0].namespaceRegexes | join(",")' "$manifest" | sed '/^$/d')" = '^(airflow|cognee|demo|langfuse|litellm|openwebui|trace-demo)$'
test "$(yq -rN 'select(.metadata.name == "vault-home-services") | .spec.conditions[0].namespaceRegexes | join(",")' "$manifest" | sed '/^$/d')" = '^(changedetection|immich|karakeep)$'

grep -Fq 'roleId: "61e40e0b-a0ca-dfc7-5bf1-507c74e9f08c"' "$manifest"
