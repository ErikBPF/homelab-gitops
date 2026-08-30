#!/usr/bin/env bash
set -euo pipefail

context=${KUBE_CONTEXT:-homelab}

exec kubectl --context "$context" -n argocd exec \
  statefulset/argocd-application-controller -- \
  argocd app delete cognee --core --cascade --yes
