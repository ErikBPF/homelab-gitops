#!/usr/bin/env bash
set -euo pipefail

if grep -R -n -E '^[[:space:]]+automated:' bootstrap clusters; then
  echo "automated Argo sync is forbidden while main is unprotected" >&2
  exit 1
fi

grep -Fq 'argocd app sync root --revision "$SHA"' README.md
grep -Fq 'argocd app sync argocd traefik csi-driver-nfs external-secrets kube-state-metrics alloy-metrics keda jaeger otel-collector vcluster-pastelariadev trace-demo demo airflow --revision "$SHA"' README.md
grep -Fq 'sync revision app="root":' justfile
grep -Fq 'serviceAccountName":"argocd-application-controller' justfile
grep -Fq 'kubectl --context {{ctx}} -n {{ns}} run "$pod" --rm -i --restart=Never' justfile
grep -Fq 'argocd app sync "{{app}}" --core --revision "{{revision}}"' justfile
