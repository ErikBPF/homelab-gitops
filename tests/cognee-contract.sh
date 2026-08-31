#!/usr/bin/env bash
set -euo pipefail

refute() {
  if "$@"; then
    return 1
  fi
}

manifest=apps/cognee/resources.yaml
application=clusters/kepler/apps/cognee.yaml
image_dir=apps/cognee/image
image_workflow=.github/workflows/cognee-image.yml
validate_workflow=.github/workflows/validate.yml

test -f "$manifest"
test -f "$application"
refute grep -Fq 'kind: Namespace' "$manifest"
grep -Fq 'cognee.yaml' clusters/kepler/apps/kustomization.yaml
test ! -e "$image_dir"
test ! -e "$image_workflow"
grep -Fq 'scripts/validate.sh' "$validate_workflow"

grep -Eq 'harbor\.homelab\.pastelariadev\.com/library/cognee-homelab@sha256:[0-9a-f]{64}' "$manifest"
grep -Fq 'pgvector/pgvector:pg18@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a' "$manifest"
grep -Fq 'falkordb/falkordb-server:v4.20.3@sha256:aaecf621b52d33aa4946a77b7d6638e709bbb9d4e76220aea115fda117f5ff36' "$manifest"
grep -Fq 'storageClassName: nfs-fast' "$manifest"
test "$(grep -Fc 'kind: PersistentVolumeClaim' "$manifest")" -eq 3

for setting in \
  'DB_PROVIDER: postgres' \
  'VECTOR_DB_PROVIDER: falkor' \
  'VECTOR_DB_URL: falkordb' \
  'VECTOR_DATASET_DATABASE_HANDLER: falkor_vector_local' \
  'GRAPH_DATABASE_PROVIDER: falkor' \
  'GRAPH_DATABASE_URL: falkordb' \
  'GRAPH_DATASET_DATABASE_HANDLER: falkor_graph_local' \
  'ENABLE_BACKEND_ACCESS_CONTROL: "true"' \
  'REQUIRE_AUTHENTICATION: "true"' \
  'HASH_API_KEY: "true"' \
  'ACCEPT_LOCAL_FILE_PATH: "false"' \
  'ALLOW_HTTP_REQUESTS: "false"' \
  'ALLOW_CYPHER_QUERY: "false"' \
  'AUTO_FEEDBACK: "false"' \
  'DATASET_QUEUE_MAX_CONCURRENT: "1"' \
  'LLM_MODEL: openai/qwen-chat' \
  'LLM_MAX_COMPLETION_TOKENS: "4096"' \
  'LLM_INSTRUCTOR_MODE: json_mode' \
  'LLM_RATE_LIMIT_REQUESTS: "30"' \
  'HOME: /var/lib/cognee/home' \
  'EMBEDDING_MODEL: openai/bge-m3' \
  'EMBEDDING_DIMENSIONS: "1024"' \
  'EMBEDDING_BATCH_SIZE: "32"' \
  'HUGGINGFACE_TOKENIZER: BAAI/bge-m3' \
  'TELEMETRY_DISABLED: "true"' \
  'HF_HUB_OFFLINE: "1"'; do
  grep -Fq "$setting" "$manifest"
done

grep -Fq 'LLM_ARGS: '\''{"max_tokens":4096,"extra_body":{"chat_template_kwargs":{"enable_thinking":false}}}'\''' "$manifest"

grep -Fq 'key: lab/cognee' "$manifest"
grep -Fq 'secretKey: COGNEE_INGEST_PASSWORD' "$manifest"
grep -Fq 'property: COGNEE_INGEST_PASSWORD' "$manifest"
grep -Fq 'name: cognee-harbor' "$manifest"
grep -Fq 'type: kubernetes.io/dockerconfigjson' "$manifest"
grep -Fq 'property: HARBOR_DOCKERCONFIGJSON' "$manifest"
grep -Fq 'imagePullSecrets: [{ name: cognee-harbor }]' "$manifest"
grep -Fq 'host: cognee.k8s.pastelariadev.com' "$manifest"
grep -Fq 'readinessProbe:' "$manifest"
grep -Fq 'startupProbe:' "$manifest"
grep -Fq 'allowPrivilegeEscalation: false' "$manifest"
grep -Fq 'capabilities: { drop: ["ALL"] }' "$manifest"
grep -Fq 'matchLabels: { kubernetes.io/metadata.name: traefik }' "$manifest"
grep -Fq 'cidr: 192.168.10.210/32' "$manifest"
grep -Fq 'value: --appendonly yes --appendfsync everysec' "$manifest"
grep -Fq 'command: [redis-cli, ping]' "$manifest"
refute grep -Fq 'matchLabel:' "$manifest"
refute grep -Fq 'cidr: 0.0.0.0/0' "$manifest"
refute grep -Fq 'deepseek-v4-flash' "$manifest"
refute grep -Fq 'GRAPH_DATABASE_PROVIDER: kuzu' "$manifest"
refute grep -Fq 'pip install' "$manifest"
refute grep -Eqi '^[[:space:]]*(POSTGRES_PASSWORD|LLM_API_KEY|FASTAPI_USERS_(JWT|VERIFICATION_TOKEN|RESET_PASSWORD_TOKEN)_SECRET):[[:space:]]+[^{$]' "$manifest"

grep -Fq 'path: apps/cognee' "$application"
grep -Fq 'namespace: cognee' "$application"
refute grep -Eq '^[[:space:]]+automated:' "$application"

for script in scripts/bootstrap-cognee-runtime.sh scripts/cognee-backup.sh scripts/cognee-restore-drill.sh; do
  test -x "$script"
done
grep -Fq 'bootstrap-cognee-runtime:' justfile
grep -Fq 'cognee-backup:' justfile
grep -Fq 'cognee-restore-drill' justfile
grep -Fq 'pg_isready -h 127.0.0.1' scripts/cognee-restore-drill.sh
grep -Fq 'pg_restore -h 127.0.0.1 -U restore -d restore --no-owner' scripts/cognee-restore-drill.sh
grep -Fq 'psql -h 127.0.0.1' scripts/cognee-restore-drill.sh
grep -Fq -- '--loadmodule /var/lib/falkordb/bin/falkordb.so' scripts/cognee-restore-drill.sh
grep -Fq 'for _ in $(seq 1 30)' scripts/cognee-restore-drill.sh
grep -Fq 'cat /tmp/redis.log >&2' scripts/cognee-restore-drill.sh
refute grep -Fq -- '--daemonize yes' scripts/cognee-restore-drill.sh

rules=platform/monitoring/files/alerting/rules.yaml
for uid in cognee-api-unavailable cognee-pvc-high cognee-backup-stale; do
  yq -e ".groups[].rules[] | select(.uid == \"$uid\")" "$rules" >/dev/null
done
