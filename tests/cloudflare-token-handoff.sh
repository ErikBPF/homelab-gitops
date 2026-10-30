#!/usr/bin/env bash
# Verify the DNS-01 handoff's manifest, cleanup command and output boundaries.
# This synthetic-client contract does not prove live Secret metadata cleanup.
set -euo pipefail

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"

cat >"$tmp/bin/kubectl" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$KUBECTL_LOG"
case "$*" in
  '--context homelab -n monitoring create secret generic cloudflare-api-token '*)
    token_file=''
    for arg in "$@"; do
      case "$arg" in --from-file=api-token=*) token_file=${arg#--from-file=api-token=} ;; esac
    done
    printf 'apiVersion: v1\nkind: Secret\nmetadata:\n  name: cloudflare-api-token\n  namespace: monitoring\ndata:\n  api-token: %s\n' "$(base64 -w0 "$token_file")"
    ;;
  *' apply '*) cat >"$KUBECTL_MANIFEST" ;;
  *' patch secret '*) ;;
  *) exit 64 ;;
esac
STUB
chmod 700 "$tmp/bin/kubectl"

export KUBECTL_LOG="$tmp/log"
export KUBECTL_MANIFEST="$tmp/manifest"
encoded_token=$(printf 'stub-token-value' | base64 -w0)
output=$(printf 'stub-token-value' | PATH="$tmp/bin:$PATH" bash "$root/scripts/bootstrap-monitoring-dns01-token.sh" 2>&1)

grep -Fxq -- '--context homelab apply --server-side --force-conflicts -f -' "$KUBECTL_LOG"
grep -Fxq -- '--context homelab -n monitoring patch secret cloudflare-api-token --type=merge -p {"metadata":{"annotations":{"kubectl.kubernetes.io/last-applied-configuration":null}}}' "$KUBECTL_LOG"
grep -Fxq 'kind: Secret' "$KUBECTL_MANIFEST"
grep -Fxq '  name: cloudflare-api-token' "$KUBECTL_MANIFEST"
grep -Fxq '  namespace: monitoring' "$KUBECTL_MANIFEST"
grep -Fxq "  api-token: $encoded_token" "$KUBECTL_MANIFEST"
if grep -Fq 'last-applied-configuration' "$KUBECTL_MANIFEST" ||
  grep -Fq -e 'stub-token-value' -e "$encoded_token" "$KUBECTL_LOG" ||
  grep -Fq -e 'stub-token-value' -e "$encoded_token" <<<"$output"; then
  echo "Token disclosure or legacy annotation detected" >&2
  exit 1
fi

echo "handoff contract OK"
