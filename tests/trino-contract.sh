#!/usr/bin/env bash
# The compute backends aster runs cells against: one Trino coordinator and one
# gateway in front of it. These are the properties the aster live checks and the
# routing-group contract depend on.
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

# The Trino applications, namespace and project destination are out of the
# app-of-apps until D6 settles; the manifests below stay contract-checked.

trino=$(kubectl kustomize "$root/platform/trino")
echo "$trino" | grep -Fq 'coordinator=true'
echo "$trino" | grep -Fq 'node-scheduler.include-coordinator=true'
echo "$trino" | grep -Fq 'connector.name=tpch'

gateway=$(kubectl kustomize "$root/platform/trino-gateway")
echo "$gateway" | grep -Fq 'jdbc:postgresql://trino-gateway-postgres:5432/gateway'
# The one backend aster routes to; the group must match ASTER_ENGINES' routing group.
echo "$gateway" | grep -Fq '"proxyTo":"http://trino:8080"'
echo "$gateway" | grep -Fq '"routingGroup":"lab"'

# aster reaches both through the namespace policy, nothing else does.
for component in trino trino-gateway; do
  kubectl kustomize "$root/platform/$component" | grep -Fq 'kubernetes.io/metadata.name: aster'
done
