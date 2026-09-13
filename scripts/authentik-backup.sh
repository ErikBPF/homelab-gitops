#!/usr/bin/env bash
set -euo pipefail
context=${KUBE_CONTEXT:-homelab}
job=authentik-backup-manual-$(date -u +%s)-$RANDOM
kubectl --context "$context" -n authentik create job --from=cronjob/authentik-backup "$job" >/dev/null
if ! kubectl --context "$context" -n authentik wait --for=condition=complete "job/$job" --timeout=12m >/dev/null; then
  echo "Authentik backup did not complete: $job" >&2
  exit 1
fi
printf 'Authentik backup completed: %s\n' "$job"
