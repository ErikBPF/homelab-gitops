#!/usr/bin/env bash
set -euo pipefail

context=${KUBE_CONTEXT:-homelab}
namespace=${COGNEE_NAMESPACE:-cognee}
root=${COGNEE_BACKUP_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/cognee/backups}
stamp=$(date -u +%Y%m%dT%H%M%SZ)
target=$root/$stamp
mkdir -p "$target"
chmod 700 "$root" "$target"

replicas=$(kubectl --context "$context" -n "$namespace" get deployment cognee -o jsonpath='{.spec.replicas}')
restore_scale() { kubectl --context "$context" -n "$namespace" scale deployment cognee --replicas="$replicas" >/dev/null; }
trap restore_scale EXIT
kubectl --context "$context" -n "$namespace" scale deployment cognee --replicas=0 >/dev/null
kubectl --context "$context" -n "$namespace" wait --for=delete pod -l app=cognee --timeout=2m >/dev/null

kubectl --context "$context" -n "$namespace" exec statefulset/postgres -- \
  pg_dump -Fc -U cognee cognee >"$target/postgres.dump"
kubectl --context "$context" -n "$namespace" exec statefulset/falkordb -- redis-cli SAVE >/dev/null
kubectl --context "$context" -n "$namespace" exec statefulset/falkordb -- \
  sh -c 'cat /var/lib/falkordb/data/dump.rdb' >"$target/falkordb.rdb"
sha256sum "$target/postgres.dump" "$target/falkordb.rdb" >"$target/SHA256SUMS"

marker="cognee-backup-$(date -u +%s)"
kubectl --context "$context" -n "$namespace" create configmap "$marker" \
  --from-literal=backup="$stamp" --dry-run=client -o yaml \
  | kubectl --context "$context" -n "$namespace" label -f - cognee.pastelariadev.com/backup=success --local -o yaml \
  | kubectl --context "$context" apply -f - >/dev/null

echo "$target"
