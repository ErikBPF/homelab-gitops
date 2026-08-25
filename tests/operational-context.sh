#!/usr/bin/env bash
set -euo pipefail

grep -Fq 'ctx := "homelab"' justfile
grep -Fq 'context=${KUBE_CONTEXT:-homelab}' scripts/capacity.sh
grep -Fq 'KUBE_CONTEXT="${KUBE_CONTEXT:-homelab}"' scripts/capture-airflow-runtime-to-vault.sh
grep -Fq 'context `homelab`' README.md
grep -Fq 'kubectl --context {{ctx}} annotate externalsecrets.external-secrets.io --all --all-namespaces' justfile
test ! -e platform/vcluster-pastelariadev/Chart.yaml
test ! -e clusters/kepler/apps/vcluster-pastelariadev.yaml
! grep -Eq '^(connect|diagnose)-pastelariadev:' justfile
