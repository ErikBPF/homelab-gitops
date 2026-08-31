#!/usr/bin/env bash
set -euo pipefail

context=${KUBE_CONTEXT:-homelab}
namespace=${COGNEE_NAMESPACE:-cognee}
root=${COGNEE_BACKUP_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/cognee/backups}
backup=${1:-$(find "$root" -mindepth 1 -maxdepth 1 -type d | sort | tail -1)}
[ -n "$backup" ] && sha256sum -c "$backup/SHA256SUMS" >/dev/null

pg_image=$(kubectl --context "$context" -n "$namespace" get statefulset postgres -o jsonpath='{.spec.template.spec.containers[0].image}')
falkor_image=$(kubectl --context "$context" -n "$namespace" get statefulset falkordb -o jsonpath='{.spec.template.spec.containers[0].image}')
cleanup() {
  kubectl --context "$context" -n "$namespace" delete pod cognee-restore-postgres cognee-restore-falkor \
    --ignore-not-found --wait=false >/dev/null
}
trap cleanup EXIT
cleanup

kubectl --context "$context" -n "$namespace" run cognee-restore-postgres --restart=Never --image="$pg_image" \
  --env=POSTGRES_PASSWORD=restore-only --env=POSTGRES_DB=restore --env=POSTGRES_USER=restore >/dev/null
kubectl --context "$context" -n "$namespace" wait --for=condition=Ready pod/cognee-restore-postgres --timeout=2m >/dev/null
kubectl --context "$context" -n "$namespace" exec -i cognee-restore-postgres -- \
  pg_restore -U restore -d restore --clean --if-exists <"$backup/postgres.dump"
kubectl --context "$context" -n "$namespace" exec cognee-restore-postgres -- \
  psql -U restore -d restore -Atc 'select count(*) from information_schema.tables' >/dev/null

kubectl --context "$context" -n "$namespace" run cognee-restore-falkor --restart=Never --image="$falkor_image" \
  --command -- sleep 600 >/dev/null
kubectl --context "$context" -n "$namespace" wait --for=condition=Ready pod/cognee-restore-falkor --timeout=2m >/dev/null
kubectl --context "$context" -n "$namespace" exec -i cognee-restore-falkor -- \
  sh -c 'cat > /tmp/dump.rdb' <"$backup/falkordb.rdb"
kubectl --context "$context" -n "$namespace" exec cognee-restore-falkor -- redis-check-rdb /tmp/dump.rdb >/dev/null
kubectl --context "$context" -n "$namespace" exec cognee-restore-falkor -- \
  sh -c 'redis-server --daemonize yes --dir /tmp --dbfilename dump.rdb && redis-cli ping | grep -Fx PONG' >/dev/null

echo "Cognee restore drill passed: $backup"
