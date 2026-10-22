#!/usr/bin/env bash
set -euo pipefail

refute() {
  if "$@"; then
    return 1
  fi
}

manifest=apps/cognee/resources.yaml
projects=clusters/kepler/apps/projects.yaml
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
grep -Fq 'harbor.homelab.pastelariadev.com/dockerhub/pgvector/pgvector:pg18@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a' "$manifest"
grep -Fq 'harbor.homelab.pastelariadev.com/dockerhub/falkordb/falkordb-server:v4.20.3@sha256:aaecf621b52d33aa4946a77b7d6638e709bbb9d4e76220aea115fda117f5ff36' "$manifest"
grep -Fq 'storageClassName: nfs-fast' "$manifest"
test "$(grep -Fc 'kind: PersistentVolumeClaim' "$manifest")" -eq 4
test "$(grep -Ec '^kind: PersistentVolume$' "$manifest")" -eq 1
yq -e 'select(.kind == "AppProject" and .metadata.name == "homelab") | .spec.clusterResourceWhitelist[] | select(.group == "" and .kind == "PersistentVolume")' "$projects" >/dev/null
for expected in \
  'name: cognee-backups' \
  'persistentVolumeReclaimPolicy: Retain' \
  'driver: nfs.csi.k8s.io' \
  'share: /fast/k8s/cognee-backups' \
  'cidr: 10.250.0.11/32' \
  'cidr: 10.250.0.12/32' \
  'cidr: 10.250.0.13/32' \
  'protocol: TCP, port: 6443' \
  'kubectl -n cognee exec pod/postgres-0' \
  'kubectl -n cognee exec pod/falkordb-0' \
  'volumeName: cognee-backups' \
  'runAsUser: 1000' \
  'runAsGroup: 100' \
  'fsGroup: 100' \
  'chmod 770 "$root" "$target"' \
  'kind: CronJob' \
  'name: cognee-backup' \
  'timeZone: America/Sao_Paulo' \
  'concurrencyPolicy: Forbid' \
  'schedule: "15 3 * * *"' \
  'COGNEE_BACKUP_RETENTION_DAYS' \
  'value: "14"' \
  'cognee-backup-script' \
  'cognee-backup-canary' \
  'RecoveryCanary' \
  'GRAPH.DELETE cognee-backup-canary' \
  'cd "$target"' \
  'sha256sum postgres.dump falkordb.rdb postgres-counts.tsv falkor-counts.tsv' \
  '/backups/cognee' \
  'harbor.homelab.pastelariadev.com/dockerhub/bitnami/kubectl:'; do
  grep -Fq "$expected" "$manifest"
done
refute grep -Fq 'kubectl -n cognee exec statefulset/' "$manifest"

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
  'LITELLM_DROP_PARAMS: "true"' \
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
grep -Fq 'securityContext:{runAsNonRoot:true,runAsUser:1000,runAsGroup:100,fsGroup:100' scripts/cognee-restore-drill.sh
grep -Fq 'cd "$backup"' scripts/cognee-restore-drill.sh
grep -Fq 'sha256sum -c SHA256SUMS' scripts/cognee-restore-drill.sh
refute grep -Fq -- '--daemonize yes' scripts/cognee-restore-drill.sh
grep -Fq 'postgres-counts.tsv' scripts/cognee-restore-drill.sh
grep -Fq 'falkor-counts.tsv' scripts/cognee-restore-drill.sh
grep -Fq 'cognee-backup-canary' scripts/cognee-restore-drill.sh
grep -Fq 'create job --from=cronjob/cognee-backup' scripts/cognee-backup.sh

# A database-only drill cannot recover retained documents or prove reader safety.
yq -o=json 'select(.kind == "CronJob" and .metadata.name == "cognee-backup") | .spec.jobTemplate.spec.template.spec.securityContext' "$manifest" \
  | jq -e '.runAsUser == 1000 and .runAsGroup == 1000 and .fsGroup == 1000' >/dev/null \
  || { echo 'backup identity must preserve application UID/GID 1000' >&2; exit 1; }
for required in 'cognee-data.tar' 'readiness.json.lock' 'flock -x' 'mountPath: /source-data'; do
  grep -Fq "$required" "$manifest" || { echo "missing full recovery backup contract: $required" >&2; exit 1; }
done
for required in 'cognee-data.tar' 'create namespace' 'policyTypes' 'Egress' 'dataset_database' \
  'vector_database_url' 'graph_database_url' '/api/v1/search' '/api/v1/datasets' 'readiness.json' \
  'raw_data_location' 'content_hash' '403' 'cognee-restore-'; do
  grep -Fq "$required" scripts/cognee-restore-drill.sh || { echo "missing full recovery restore contract: $required" >&2; exit 1; }
done

rules=platform/monitoring/files/alerting/rules.yaml
for uid in cognee-api-unavailable cognee-pvc-high cognee-backup-stale; do
  yq -e ".groups[].rules[] | select(.uid == \"$uid\")" "$rules" >/dev/null
done

# C6 must compare identities, not only row counts.
python3 tests/cognee-restore-identity.py
