#!/usr/bin/env bash
set -euo pipefail

context=${KUBE_CONTEXT:-homelab}
namespace=${COGNEE_NAMESPACE:-cognee}
job=cognee-backup-manual-$(date -u +%s)
kubectl --context "$context" -n "$namespace" create job --from=cronjob/cognee-backup "$job" >/dev/null
if ! kubectl --context "$context" -n "$namespace" wait --for=condition=complete "job/$job" --timeout=15m >/dev/null; then
  kubectl --context "$context" -n "$namespace" logs "job/$job" >&2 || true
  exit 1
fi
kubectl --context "$context" -n "$namespace" logs "job/$job"
