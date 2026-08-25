#!/usr/bin/env bash
set -euo pipefail

revision=${1:-}
app=${2:-root}
context=${KUBE_CONTEXT:-homelab}

if [[ ! $revision =~ ^[0-9a-f]{40}$ ]]; then
  echo "revision must be a full 40-character commit SHA" >&2
  exit 2
fi
if [[ ! $app =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]]; then
  echo "invalid Argo application name: $app" >&2
  exit 2
fi

exec kubectl --context "$context" -n argocd exec \
  statefulset/argocd-application-controller -- \
  argocd app sync "$app" --core --revision "$revision"
