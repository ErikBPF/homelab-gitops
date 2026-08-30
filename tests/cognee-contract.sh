#!/usr/bin/env bash
set -euo pipefail

refute() {
  if "$@"; then
    return 1
  fi
}

manifest=apps/cognee/resources.yaml
image_dir=apps/cognee/image
image_workflow=.github/workflows/cognee-image.yml
validate_workflow=.github/workflows/validate.yml

test -f "$manifest"
test ! -e clusters/kepler/apps/cognee.yaml
refute grep -Fq 'cognee.yaml' clusters/kepler/apps/kustomization.yaml
test ! -e "$image_dir"
test ! -e "$image_workflow"
grep -Fq 'scripts/validate.sh' "$validate_workflow"

grep -Fq 'harbor.homelab.pastelariadev.com/library/cognee-homelab:1.4.2-adapter0.4.0-batch2' "$manifest"
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
