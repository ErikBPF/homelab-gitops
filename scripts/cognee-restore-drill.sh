#!/usr/bin/env bash
set -euo pipefail

context=${KUBE_CONTEXT:-homelab}
namespace=${COGNEE_NAMESPACE:-cognee}
pg_image=$(kubectl --context "$context" -n "$namespace" get statefulset postgres -o jsonpath='{.spec.template.spec.containers[0].image}')
falkor_image=$(kubectl --context "$context" -n "$namespace" get statefulset falkordb -o jsonpath='{.spec.template.spec.containers[0].image}')
helper_image=$(kubectl --context "$context" -n "$namespace" get cronjob cognee-backup -o jsonpath='{.spec.jobTemplate.spec.template.spec.containers[0].image}')
helper=cognee-backup-reader-$(date -u +%s)
staging=
cleanup() {
  kubectl --context "$context" -n "$namespace" delete pod cognee-restore-postgres cognee-restore-falkor "$helper" \
    --ignore-not-found --wait=false >/dev/null
  [[ -z $staging ]] || rm -rf "$staging"
}
trap cleanup EXIT
cleanup

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
    sh -c 'find /backups/cognee -mindepth 1 -maxdepth 1 -type d | sort | tail -1')
  [[ $latest == /backups/cognee/* ]] || { echo "no scheduled Cognee backup found" >&2; exit 1; }
  for file in postgres.dump falkordb.rdb postgres-counts.tsv falkor-counts.tsv SHA256SUMS; do
    kubectl --context "$context" -n "$namespace" exec "$helper" -- cat "$latest/$file" >"$staging/$file"
  done
  backup=$staging
fi
sha256sum -c "$backup/SHA256SUMS" >/dev/null

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
  pg_restore -h 127.0.0.1 -U restore -d restore --no-owner --clean --if-exists <"$backup/postgres.dump"
kubectl --context "$context" -n "$namespace" exec cognee-restore-postgres -- \
  psql -h 127.0.0.1 -U restore -d restore -Atc 'select count(*) from information_schema.tables' >/dev/null
while IFS=$'\t' read -r table expected; do
  [[ $table =~ ^[a-z_]+$ && $expected =~ ^[0-9]+$ ]] || { echo "invalid Postgres count manifest" >&2; exit 1; }
  actual=$(kubectl --context "$context" -n "$namespace" exec cognee-restore-postgres -- \
    psql -h 127.0.0.1 -U restore -d restore -Atc "select count(*) from $table")
  [[ $actual == "$expected" ]] || { echo "Postgres restore count mismatch: $table" >&2; exit 1; }
done <"$backup/postgres-counts.tsv"
grep -Fq $'cognee_backup_canary\t1' "$backup/postgres-counts.tsv"

kubectl --context "$context" -n "$namespace" run cognee-restore-falkor --restart=Never --image="$falkor_image" \
  --command -- sleep 600 >/dev/null
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

echo "Cognee restore drill passed: $backup"
