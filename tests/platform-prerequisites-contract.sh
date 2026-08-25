#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

test "$(yq -r '.dependencies[0].repository' "$root/platform/cert-manager/Chart.yaml")" = oci://quay.io/jetstack/charts
test "$(yq -r '.dependencies[0].version' "$root/platform/cert-manager/Chart.yaml")" = v1.20.2
test "$(yq -r '.cert-manager.crds.enabled' "$root/platform/cert-manager/values.yaml")" = true

test "$(yq -r '.dependencies[0].repository' "$root/platform/clickhouse-operator/Chart.yaml")" = oci://ghcr.io/clickhouse
test "$(yq -r '.dependencies[0].version' "$root/platform/clickhouse-operator/Chart.yaml")" = 0.0.5

for app in cert-manager clickhouse-operator; do
  manifest=$root/clusters/kepler/apps/$app.yaml
  test "$(yq -r '.spec.project' "$manifest")" = platform
  test "$(yq -r '.spec.source.path' "$manifest")" = "platform/$app"
  test "$(yq -r '.spec.destination.namespace' "$manifest")" = "$app"
done

test "$(yq -r '.metadata.annotations."argocd.argoproj.io/sync-wave"' "$root/clusters/kepler/apps/cert-manager.yaml")" = -4
test "$(yq -r '.metadata.annotations."argocd.argoproj.io/sync-wave"' "$root/clusters/kepler/apps/clickhouse-operator.yaml")" = -3

grep -Fq '{group: clickhouse.com, kind: ClickHouseCluster}' "$root/clusters/kepler/apps/projects.yaml"
grep -Fq '{group: clickhouse.com, kind: KeeperCluster}' "$root/clusters/kepler/apps/projects.yaml"
