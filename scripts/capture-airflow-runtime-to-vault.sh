#!/usr/bin/env bash
set -euo pipefail

KUBE_CONTEXT="${KUBE_CONTEXT:-pastelariadev}"
BAO_API="${BAO_API:-http://discovery:8200/v1}"
vault_path="$BAO_API/secret/data/lab/airflow"

IFS= read -r bao_token
[ -n "$bao_token" ] || { echo "OpenBao operator token required on stdin" >&2; exit 2; }
header="$(mktemp)"
trap 'shred -u "$header" 2>/dev/null || rm -f "$header"' EXIT
chmod 600 "$header"
printf 'X-Vault-Token: %s\n' "$bao_token" > "$header"
unset bao_token

values="$(helm --kube-context "$KUBE_CONTEXT" -n airflow get values airflow -o json)"
env_value() {
  jq -er --arg name "$1" 'first(.env[] | select(.name == $name) | .value)' <<<"$values"
}
secret_value() {
  kubectl --context "$KUBE_CONTEXT" -n airflow get secret "$1" \
    -o "jsonpath={.data.$2}" | base64 --decode
}

environment="$(env_value AIRFLOW_VAR_ENVIRONMENT)"
connections="$(env_value AIRFLOW_VAR_DATAPLATFORM_DEFAULT_CONNECTIONS)"
spark="$(env_value AIRFLOW_VAR_DATAPLATFORM_DEFAULT_SPARK_CONFIG)"
execution="$(env_value AIRFLOW_VAR_DATAPLATFORM_DEFAULT_EXECUTION)"
dbt_cache="$(jq -er 'first(.scheduler.env[] | select(.name == "DBT_MANIFEST_CACHE_DIR") | .value)' <<<"$values")"
fernet="$(secret_value airflow-fernet-key fernet-key)"
api_secret="$(secret_value airflow-api-secret-key api-secret-key)"
jwt="$(secret_value airflow-jwt-secret jwt-secret)"
metadata="$(secret_value airflow-metadata connection)"
broker="$(secret_value airflow-broker-url connection)"
unset values

method=PATCH
content_type=application/merge-patch+json
if ! curl -fsS --header @"$header" "$vault_path" >/dev/null; then
  method=POST
  content_type=application/json
fi
jq -nc \
  --arg environment "$environment" \
  --arg connections "$connections" \
  --arg spark "$spark" \
  --arg execution "$execution" \
  --arg dbt_cache "$dbt_cache" \
  --arg fernet "$fernet" \
  --arg api_secret "$api_secret" \
  --arg jwt "$jwt" \
  --arg metadata "$metadata" \
  --arg broker "$broker" \
  '{data:{
    AIRFLOW_VAR_ENVIRONMENT:$environment,
    AIRFLOW_VAR_DATAPLATFORM_DEFAULT_CONNECTIONS:$connections,
    AIRFLOW_VAR_DATAPLATFORM_DEFAULT_SPARK_CONFIG:$spark,
    AIRFLOW_VAR_DATAPLATFORM_DEFAULT_EXECUTION:$execution,
    DBT_MANIFEST_CACHE_DIR:$dbt_cache,
    FERNET_KEY:$fernet,
    API_SECRET_KEY:$api_secret,
    JWT_SECRET:$jwt,
    METADATA_CONNECTION:$metadata,
    BROKER_URL:$broker
  }}' \
  | curl -fsS --request "$method" \
      --header @"$header" \
      --header "Content-Type: $content_type" \
      --data-binary @- "$vault_path" >/dev/null

unset environment connections spark execution dbt_cache fernet api_secret jwt metadata broker
echo "airflow runtime secrets captured in Vault"
