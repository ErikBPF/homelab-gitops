#!/usr/bin/env bash
set -euo pipefail
umask 077

context=${KUBE_CONTEXT:-homelab}
namespace=${COGNEE_NAMESPACE:-cognee}
pg_image=$(kubectl --context "$context" -n "$namespace" get statefulset postgres -o jsonpath='{.spec.template.spec.containers[0].image}')
falkor_image=$(kubectl --context "$context" -n "$namespace" get statefulset falkordb -o jsonpath='{.spec.template.spec.containers[0].image}')
helper_image=$(kubectl --context "$context" -n "$namespace" get cronjob cognee-backup -o jsonpath='{.spec.jobTemplate.spec.template.spec.containers[0].image}')
production_namespace=$namespace
restore_namespace=cognee-restore-$(date -u +%s)-$RANDOM
helper=cognee-backup-reader-$(date -u +%s)-$RANDOM
created_namespace=false
staging=
member_list=
evidence=$(mktemp -d /tmp/cognee-restore-state.XXXXXX)
production_state() {
  printf 'postgres '
  kubectl --context "$context" -n "$production_namespace" exec postgres-0 -- \
    psql -At -U cognee -d cognee -c "select md5(coalesce(string_agg(value, ',' order by value), '')) from (select row_to_json(d)::text value from datasets d union all select row_to_json(a)::text from acls a union all select row_to_json(u)::text from users u union all select row_to_json(d)::text from data d union all select row_to_json(d)::text from dataset_database d) state"
  # Expand graph names inside the database pod, not the operator shell.
  # shellcheck disable=SC2016
  kubectl --context "$context" -n "$production_namespace" exec falkordb-0 -- sh -c \
    'redis-cli --raw GRAPH.LIST | sort | while IFS= read -r graph; do printf "graph %s " "$graph"; redis-cli --raw DUMP "$graph" | sha256sum; done'
  printf 'files '
  kubectl --context "$context" -n "$production_namespace" exec deployment/cognee -- sh -c \
    'find /var/lib/cognee/data -type f -exec sha256sum {} + | sort | sha256sum'
}
production_before=$(production_state)
printf '%s\n' "$production_before" >"$evidence/before"
echo "Production state evidence: $evidence"
cleanup() {
  result=$?
  set +e
  kubectl --context "$context" -n "$production_namespace" delete pod "$helper" --ignore-not-found --wait=false >/dev/null
  if [[ $created_namespace == true ]]; then
    kubectl --context "$context" delete namespace "$restore_namespace" --wait=true --timeout=120s >/dev/null || result=1
  fi
  [[ -z $staging ]] || rm -rf "$staging"
  [[ -z $member_list ]] || rm -f "$member_list"
  production_after=$(production_state)
  printf '%s\n' "$production_after" >"$evidence/after"
  if [[ $production_after != "$production_before" ]]; then
    echo "production state changed during isolated restore" >&2
    result=1
  else
    echo "Production state unchanged"
  fi
  if [[ $result == 0 ]]; then
    echo "Cognee full restore drill passed; production state unchanged ($restore_namespace)"
  fi
  exit "$result"
}
trap cleanup EXIT

backup=${1:-}
if [[ -z $backup ]]; then
  staging=$(mktemp -d)
  jq -n --arg name "$helper" --arg image "$helper_image" '{
    apiVersion:"v1",kind:"Pod",metadata:{name:$name},spec:{restartPolicy:"Never",
      securityContext:{runAsNonRoot:true,runAsUser:1000,runAsGroup:100,fsGroup:100,seccompProfile:{type:"RuntimeDefault"}},
      containers:[{name:"reader",image:$image,command:["sleep","600"],securityContext:{allowPrivilegeEscalation:false,capabilities:{drop:["ALL"]}},volumeMounts:[{name:"backups",mountPath:"/backups",readOnly:true}]}],
      volumes:[{name:"backups",persistentVolumeClaim:{claimName:"cognee-backups"}}]}}
  ' | kubectl --context "$context" -n "$namespace" apply -f - >/dev/null
  kubectl --context "$context" -n "$namespace" wait --for=condition=Ready "pod/$helper" --timeout=2m >/dev/null
  latest=$(kubectl --context "$context" -n "$namespace" exec "$helper" -- \
    sh -c 'find /backups/cognee -mindepth 2 -maxdepth 2 -type f -name SHA256SUMS | sort | tail -1 | xargs -r dirname')
  [[ $latest == /backups/cognee/* ]] || { echo "no scheduled Cognee backup found" >&2; exit 1; }
  for file in postgres.dump falkordb.rdb postgres-counts.tsv falkor-counts.tsv cognee-data.tar identity-digest.sha256 SHA256SUMS; do
    kubectl --context "$context" -n "$namespace" exec "$helper" -- cat "$latest/$file" >"$staging/$file"
  done
  backup=$staging
fi
(
  cd "$backup"
  sha256sum -c SHA256SUMS >/dev/null
)

# Reject unsafe archive entries before extraction into the restored application.
member_list=$(mktemp)
python3 - "$backup/cognee-data.tar" "$member_list" <<'PY_ARCHIVE'
import pathlib, sys, tarfile
with tarfile.open(sys.argv[1]) as archive, open(sys.argv[2], 'wb') as members:
    for entry in archive:
        path = pathlib.PurePosixPath(entry.name)
        if path.is_absolute() or '..' in path.parts or not (entry.isfile() or entry.isdir()):
            raise SystemExit('unsafe application archive entry')
        if path.parts:
            members.write(entry.name.encode('utf-8', 'surrogateescape') + b'\0')
PY_ARCHIVE

# A separate namespace keeps persisted short database names away from production.
kubectl --context "$context" create namespace "$restore_namespace" >/dev/null
created_namespace=true
namespace=$restore_namespace
kubectl --context "$context" -n "$namespace" apply -f - >/dev/null <<'YAML'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: restore-isolation}
spec:
  podSelector: {}
  policyTypes: [Ingress, Egress]
  ingress:
    - from: [{podSelector: {}}]
  egress:
    - to: [{podSelector: {}}]
      ports: [{protocol: TCP, port: 5432}, {protocol: TCP, port: 6379}, {protocol: TCP, port: 8000}]
    - to: [{namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: kube-system}}}]
      ports: [{protocol: UDP, port: 53}, {protocol: TCP, port: 53}]
    - to: [{ipBlock: {cidr: 192.168.10.210/32}}]
      ports: [{protocol: TCP, port: 443}]
YAML
kubectl --context "$context" -n "$namespace" run cognee-restore-postgres --restart=Never --image="$pg_image" \
  --env=POSTGRES_PASSWORD=restore-only --env=POSTGRES_DB=restore --env=POSTGRES_USER=restore >/dev/null
kubectl --context "$context" -n "$namespace" wait --for=condition=Ready pod/cognee-restore-postgres --timeout=2m >/dev/null
for _ in $(seq 1 60); do
  kubectl --context "$context" -n "$namespace" exec cognee-restore-postgres -- \
    pg_isready -h 127.0.0.1 -U restore -d restore >/dev/null 2>&1 && break
  sleep 2
done
kubectl --context "$context" -n "$namespace" exec cognee-restore-postgres -- \
  pg_isready -h 127.0.0.1 -U restore -d restore >/dev/null
kubectl --context "$context" -n "$namespace" exec -i cognee-restore-postgres -- \
  pg_restore -h 127.0.0.1 -U restore -d restore --no-owner --exit-on-error --clean --if-exists <"$backup/postgres.dump"
kubectl --context "$context" -n "$namespace" exec cognee-restore-postgres -- \
  psql -h 127.0.0.1 -U restore -d restore -Atc 'select count(*) from information_schema.tables' >/dev/null
while IFS=$'\t' read -r table expected; do
  [[ $table =~ ^[a-z_]+$ && $expected =~ ^[0-9]+$ ]] || { echo "invalid Postgres count manifest" >&2; exit 1; }
  actual=$(kubectl --context "$context" -n "$namespace" exec cognee-restore-postgres -- \
    psql -h 127.0.0.1 -U restore -d restore -Atc "select count(*) from $table")
  [[ $actual == "$expected" ]] || { echo "Postgres restore count mismatch: $table" >&2; exit 1; }
done <"$backup/postgres-counts.tsv"
grep -Fq $'cognee_backup_canary\t1' "$backup/postgres-counts.tsv"

# Compare identity rows before restored application mutations.
expected_identity=$(cat "$backup/identity-digest.sha256")
[[ $expected_identity =~ ^[0-9a-f]{64}$ ]] || { echo "invalid identity digest manifest" >&2; exit 1; }
actual_identity=$(kubectl --context "$context" -n "$namespace" exec cognee-restore-postgres -- \
  psql -At -h 127.0.0.1 -U restore -d restore -c "select encode(sha256(convert_to(coalesce(string_agg(value, E'\n' order by value), ''), 'UTF8')), 'hex') from (select jsonb_build_array('users', to_jsonb(u))::text value from users u union all select jsonb_build_array('datasets', to_jsonb(d))::text from datasets d union all select jsonb_build_array('acls', to_jsonb(a))::text from acls a) identity_rows")
[[ $actual_identity == "$expected_identity" ]] || { echo "restored ownership or grants differ from backup" >&2; exit 1; }
# Identity comparison complete.

kubectl --context "$context" -n "$namespace" run cognee-restore-falkor --restart=Never --image="$falkor_image" \
  --command -- sleep 7200 >/dev/null
kubectl --context "$context" -n "$namespace" wait --for=condition=Ready pod/cognee-restore-falkor --timeout=2m >/dev/null
kubectl --context "$context" -n "$namespace" exec -i cognee-restore-falkor -- \
  sh -c 'cat > /tmp/dump.rdb' <"$backup/falkordb.rdb"
kubectl --context "$context" -n "$namespace" exec cognee-restore-falkor -- redis-check-rdb /tmp/dump.rdb >/dev/null
# The loop expands inside the restore pod, not in this operator shell.
# shellcheck disable=SC2016
kubectl --context "$context" -n "$namespace" exec cognee-restore-falkor -- \
  sh -c 'redis-server --dir /tmp --dbfilename dump.rdb --loadmodule /var/lib/falkordb/bin/falkordb.so >/tmp/redis.log 2>&1 & for _ in $(seq 1 30); do redis-cli ping 2>/dev/null | grep -Fx PONG && exit 0; sleep 1; done; cat /tmp/redis.log >&2; exit 1' >/dev/null

while IFS=$'\t' read -r graph expected_nodes expected_edges; do
  [[ $graph =~ ^[A-Za-z0-9_.-]+$ && $expected_nodes =~ ^[0-9]+$ && $expected_edges =~ ^[0-9]+$ ]] \
    || { echo "invalid Falkor count manifest" >&2; exit 1; }
  actual_nodes=$(kubectl --context "$context" -n "$namespace" exec cognee-restore-falkor -- \
    sh -c "redis-cli --raw GRAPH.QUERY '$graph' 'MATCH (n) RETURN count(n)' | sed -n '2p'")
  actual_edges=$(kubectl --context "$context" -n "$namespace" exec cognee-restore-falkor -- \
    sh -c "redis-cli --raw GRAPH.QUERY '$graph' 'MATCH ()-[r]->() RETURN count(r)' | sed -n '2p'")
  [[ $actual_nodes == "$expected_nodes" && $actual_edges == "$expected_edges" ]] \
    || { echo "Falkor restore count mismatch: $graph" >&2; exit 1; }
done <"$backup/falkor-counts.tsv"
grep -Fq $'cognee-backup-canary\t2\t1' "$backup/falkor-counts.tsv"

# Persisted provider URLs must be namespace-local before any restored app starts.
invalid=$(kubectl --context "$context" -n "$namespace" exec cognee-restore-postgres -- \
  psql -At -h 127.0.0.1 -U restore -d restore -c "select count(*) from dataset_database where vector_database_url is distinct from 'falkordb' or graph_database_url is distinct from 'falkordb'")
[[ $invalid == 0 ]] || { echo "unsafe persisted dataset_database endpoint" >&2; exit 1; }
kubectl --context "$context" -n "$namespace" expose pod cognee-restore-postgres --name=postgres --port=5432 >/dev/null
kubectl --context "$context" -n "$namespace" expose pod cognee-restore-falkor --name=falkordb --port=6379 >/dev/null

# Keep only embedding and operator login credentials; exclude production database credentials.
kubectl --context "$context" -n "$production_namespace" get secret cognee-runtime -o json \
  | jq --arg ns "$namespace" '{apiVersion:"v1",kind:"Secret",metadata:{name:"cognee-runtime",namespace:$ns},data:(.data | with_entries(select(.key == "LLM_API_KEY" or .key == "COGNEE_INGEST_PASSWORD")))}' \
  | kubectl --context "$context" -n "$namespace" apply -f - >/dev/null
kubectl --context "$context" -n "$production_namespace" get secret cognee-harbor -o json \
  | jq --arg ns "$namespace" '{apiVersion:"v1",kind:"Secret",type:.type,metadata:{name:"cognee-harbor",namespace:$ns},data:.data}' \
  | kubectl --context "$context" -n "$namespace" apply -f - >/dev/null
kubectl --context "$context" -n "$production_namespace" get configmap cognee -o json \
  | jq --arg ns "$namespace" '{apiVersion:"v1",kind:"ConfigMap",metadata:{name:"cognee",namespace:$ns},data:(.data + {DB_HOST:"postgres",DB_USERNAME:"restore",DB_NAME:"restore",DB_PASSWORD:"restore-only",FASTAPI_USERS_JWT_SECRET:"isolated-restore-only-jwt",FASTAPI_USERS_VERIFICATION_TOKEN_SECRET:"isolated-restore-only-verification",FASTAPI_USERS_RESET_PASSWORD_TOKEN_SECRET:"isolated-restore-only-reset"})}' \
  | kubectl --context "$context" -n "$namespace" apply -f - >/dev/null
kubectl --context "$context" -n "$production_namespace" get deployment cognee -o json \
  | jq '{apiVersion:"v1",kind:"Pod",metadata:{name:"cognee",labels:{app:"cognee"}},spec:(.spec.template.spec | .restartPolicy="Never" | .volumes=[{name:"data",emptyDir:{}}] | .containers[0].env=((.containers[0].env // [] | map(select(.name == "COGNEE_READER_DOMAINS"))) + [{name:"EMBEDDING_API_KEY",valueFrom:{secretKeyRef:{name:"cognee-runtime",key:"LLM_API_KEY"}}}]) | .initContainers=[{name:"restore-files",image:.containers[0].image,command:["sh","-c","until test -f /var/lib/cognee/.restore-complete; do sleep 1; done"],volumeMounts:[{name:"data",mountPath:"/var/lib/cognee"}]}])}' \
  | kubectl --context "$context" -n "$namespace" apply -f - >/dev/null
for _ in $(seq 1 120); do
  state=$(kubectl --context "$context" -n "$namespace" get pod cognee -o jsonpath='{.status.initContainerStatuses[0].state.running.startedAt}')
  [[ -n $state ]] && break
  sleep 2
done
[[ -n $state ]] || { echo "restore file container failed to start" >&2; exit 1; }
kubectl --context "$context" -n "$namespace" exec -i cognee -c restore-files -- tar --no-overwrite-dir --same-permissions -C /var/lib/cognee -xf - <"$backup/cognee-data.tar"
kubectl --context "$context" -n "$namespace" exec -i cognee -c restore-files -- sh -c 'cat > /tmp/restore-archive-members' <"$member_list"
# Compare every file and subdirectory, including modes; Kubernetes owns only mount-root metadata.
kubectl --context "$context" -n "$namespace" exec -i cognee -c restore-files -- tar -C /var/lib/cognee -df - --no-recursion --null -T /tmp/restore-archive-members <"$backup/cognee-data.tar"
kubectl --context "$context" -n "$namespace" exec cognee -c restore-files -- touch /var/lib/cognee/.restore-complete
kubectl --context "$context" -n "$namespace" wait --for=condition=Ready pod/cognee --timeout=5m >/dev/null
# Companion proof checks raw_data_location, content_hash, readiness.json and
# /api/v1/search plus /api/v1/datasets denied-reader mutation (403).
python3 scripts/cognee-restore-proof.py "$context" "$namespace"
