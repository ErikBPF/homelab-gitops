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

echo "Cognee restore drill passed: $backup"
