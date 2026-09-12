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

state=$(kubectl --context "$context" -n argocd get application "$app" -o json)
# Guard against replacing another operator's request, including a concurrent one.
patch=$(jq -ce --arg revision "$revision" '
  if .operation != null then error("application already has an active operation")
  else [
    {op:"test",path:"/metadata/resourceVersion",value:.metadata.resourceVersion},
    {op:"add",path:"/operation",value:{sync:{revision:$revision,syncStrategy:{hook:{}},syncOptions:(.spec.syncPolicy.syncOptions // [])}}}
  ] end
' <<<"$state")

# This submits the operation; completion and health are checked separately.
exec kubectl --context "$context" -n argocd patch application "$app" --type=json --patch "$patch"
