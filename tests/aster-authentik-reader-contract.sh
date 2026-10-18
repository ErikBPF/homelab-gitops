#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
stores=$root/platform/external-secrets/templates/clustersecretstore-vault.yaml
secret=$root/apps/homelab/aster/authentik-reader-externalsecret.yaml
deployment=$root/apps/homelab/aster/deployment-server.yaml

test -f "$secret"
test "$(yq -rN 'select(.metadata.name == "vault-aster-authentik-reader") | .spec.conditions[0].namespaceRegexes[0]' "$stores" | sed '/^$/d')" = '^aster$'
test "$(yq -rN 'select(.metadata.name == "vault-aster-authentik-reader") | .spec.provider.vault.auth.appRole.roleRef | [.name,.key,.namespace] | join(",")' "$stores" | sed '/^$/d')" = 'vault-approle-aster-authentik-reader,role_id,external-secrets'
test "$(yq -rN 'select(.metadata.name == "vault-aster-authentik-reader") | .spec.provider.vault.auth.appRole.secretRef | [.name,.key,.namespace] | join(",")' "$stores" | sed '/^$/d')" = 'vault-approle-aster-authentik-reader,secret_id,external-secrets'
test "$(yq -r '.metadata.namespace + "," + .spec.secretStoreRef.name + "," + .spec.target.name' "$secret")" = 'aster,vault-aster-authentik-reader,aster-authentik-reader-secret'
test "$(yq -r '.spec.data[0] | .secretKey + "," + .remoteRef.key + "," + .remoteRef.property' "$secret")" = 'authentik_read_token,platform/aster/authentik-reader,authentik_read_token'
test "$(yq -r '.spec.data | length' "$secret")" = 1
! grep -Fq 'ASTER_AUTHENTIK_READ_TOKEN' "$deployment"
! grep -Fq 'ASTER_SHARED_MODEL_AUTHORITY_ENABLED' "$deployment"
! grep -Fq 'ASTER_SHARED_MODEL_USE_ENABLED' "$deployment"

echo 'aster-authentik-reader-contract OK'
