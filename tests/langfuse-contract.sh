#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
app=$root/apps/homelab/langfuse
render=$(mktemp)
trap 'rm -f "$render"' EXIT

test "$(yq -r '.dependencies[0].repository' "$app/Chart.yaml")" = https://langfuse.github.io/langfuse-k8s
test "$(yq -r '.spec.project' "$root/clusters/kepler/apps/langfuse.yaml")" = homelab
expected_ignored_paths=$(printf '%s\n' \
  '.spec.template.spec.serviceAccount' \
  '.spec.volumeClaimTemplates[]?.apiVersion' \
  '.spec.volumeClaimTemplates[]?.kind' \
  '.spec.volumeClaimTemplates[]?.spec.volumeMode' \
  '.spec.volumeClaimTemplates[]?.status' | sort)
actual_ignored_paths=$(yq -r '.spec.ignoreDifferences[] | select(.group == "apps" and .kind == "StatefulSet") | .jqPathExpressions[]' "$root/clusters/kepler/apps/langfuse.yaml" | sort)
test "$actual_ignored_paths" = "$expected_ignored_paths"
grep -Fq 'langfuse.yaml' "$root/clusters/kepler/apps/kustomization.yaml"

helm template langfuse "$app" >"$render"

if grep -Eq '^kind: ClusterRole(Binding)?$' "$render"; then
  echo 'Langfuse rendered cluster-wide RBAC outside the platform lane' >&2
  exit 1
fi

for store in postgresql redis clickhouse s3; do
  test "$(yq -r ".langfuse.$store.deploy" "$app/values.yaml")" = true
done
test "$(yq -r '.langfuse.clickhouse.crdCheck' "$app/values.yaml")" = false
test "$(yq -r '.langfuse.clickhouse.cluster.resources.requests.cpu' "$app/values.yaml")" = 200m
if grep -Ei '100\.76\.140\.121|192\.168\.10\.230|postgres\.discovery|minio.*kepler' "$render"; then
  echo 'Langfuse rendered a legacy data-service endpoint' >&2
  exit 1
fi

for secret in langfuse-app-secrets langfuse-postgresql-auth langfuse-redis-auth langfuse-clickhouse-auth langfuse-s3-auth; do
  test "$(yq -r "select(.kind == \"ExternalSecret\" and .spec.target.name == \"$secret\") | .spec.secretStoreRef.name" "$render")" = vault-homelab
  test "$(yq -r "select(.kind == \"ExternalSecret\" and .spec.target.name == \"$secret\") | .spec.data[0].remoteRef.key" "$render")" = lab/langfuse
done

test "$(yq -r 'select(.kind == "Ingress") | .spec.rules[0].host' "$render")" = langfuse.homelab.pastelariadev.com
test "$(yq -r 'select(.kind == "NetworkPolicy" and .metadata.name == "default-deny") | .spec.policyTypes | join(",")' "$render")" = Ingress,Egress
for class in local-path nfs-slow; do
  yq -e "select(.kind == \"PersistentVolumeClaim\" and .spec.storageClassName == \"$class\")" "$render" >/dev/null
done
python3 "$root/scripts/check-first-wave-resources.py" "$render"
python3 - "$render" <<'PY'
import sys

import yaml

with open(sys.argv[1]) as stream:
  documents = list(yaml.safe_load_all(stream))

for document in documents:
    if not document:
        continue
    spec = document.get("spec", {})
    if document.get("kind") == "CronJob":
        spec = spec.get("jobTemplate", {}).get("spec", {})
    pod = spec.get("template", {}).get("spec", {})
    for container in pod.get("initContainers", []) + pod.get("containers", []):
        image = container.get("image", "")
        assert "@sha256:" in image, f"mutable image: {image}"
PY
