#!/usr/bin/env bash
# Proves the DNS-01 handoff leaves no readable copy of the token in the
# Secret's metadata. Runs against a stub kubectl, no cluster needed.
set -euo pipefail

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"

cat >"$tmp/bin/kubectl" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$KUBECTL_LOG"
case "$1" in
  --context) printf 'apiVersion: v1\nkind: Secret\nmetadata:\n  name: cloudflare-api-token\n' ;;
  *) cat >/dev/null 2>&1 || true ;;
esac
STUB
chmod 700 "$tmp/bin/kubectl"

export KUBECTL_LOG="$tmp/log"
output=$(printf 'stub-token-value' | PATH="$tmp/bin:$PATH" bash "$root/scripts/bootstrap-monitoring-dns01-token.sh")

grep -q 'apply --server-side --force-conflicts' "$KUBECTL_LOG"
grep -q 'patch secret cloudflare-api-token' "$KUBECTL_LOG"
grep -q '"kubectl.kubernetes.io/last-applied-configuration":null' "$KUBECTL_LOG"
! grep -q 'stub-token-value' "$KUBECTL_LOG"
! grep -q 'stub-token-value' <<<"$output"

echo "handoff contract OK"
