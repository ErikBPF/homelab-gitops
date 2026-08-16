#!/usr/bin/env bash
set -euo pipefail

chart=apps/airflow/Chart.yaml
values=apps/airflow/values.yaml
secrets=apps/airflow/templates/externalsecrets.yaml
application=clusters/kepler/apps/airflow.yaml
capture=scripts/capture-airflow-runtime-to-vault.sh

grep -Fq 'version: 1.22.0' "$chart"
grep -Fq 'digest: sha256:179ffa3e6978e1bcc060f7baf163105cd8283ad6966568629f69f24ce9b79b9f' "$values"
grep -Fq 'name: airflow-harbor' "$values"
grep -Fq 'metadataSecretName: airflow-metadata' "$values"
grep -Fq 'fernetKeySecretName: airflow-fernet-key' "$values"
grep -Fq 'apiSecretKeySecretName: airflow-api-secret-key' "$values"
grep -Fq 'jwtSecretName: airflow-jwt-secret' "$values"
grep -Fq 'name: airflow-runtime' "$values"
grep -Fq 'podTemplate: |' "$values"
! grep -Fq 'apache/airflow:' "$values"
grep -Fq 'key: lab/airflow' "$secrets"
grep -Fq 'type: kubernetes.io/dockerconfigjson' "$secrets"
! grep -Eq '^\s+automated:' "$application"
test "$(grep -Fc 'kind: PersistentVolumeClaim' "$application")" -eq 2
grep -Fq 'name: airflow-dags' "$application"
grep -Fq 'name: airflow-logs' "$application"
test "$(grep -Fc -- '- /spec/volumeName' "$application")" -eq 2
grep -Fq -- '- RespectIgnoreDifferences=true' "$application"
! grep -Eqi '(password|secret):[[:space:]]+[^~]' "$values"
grep -Fq 'secret/data/lab/airflow' "$capture"
grep -Fq 'application/merge-patch+json' "$capture"
grep -Fq 'helm --kube-context' "$capture"
grep -Fq 'kubectl --context' "$capture"
grep -Fq 'read -r bao_token || [ -n "$bao_token" ]' "$capture"
grep -Fq 'BAO_SSH' "$capture"
! grep -Fq 'set -x' "$capture"
