#!/usr/bin/env bash
set -euo pipefail

IFS= read -r api_token || [ -n "$api_token" ]
[ -n "$api_token" ] || { echo "Cloudflare API token required on stdin" >&2; exit 2; }

tmp=$(mktemp -d)
chmod 700 "$tmp"
cleanup() {
  unset api_token
  find "$tmp" -type f -exec shred -u {} + 2>/dev/null || true
  rmdir "$tmp" 2>/dev/null || true
}
trap cleanup EXIT
printf '%s' "$api_token" >"$tmp/api-token"
unset api_token
chmod 600 "$tmp/api-token"

kubectl --context homelab -n monitoring create secret generic cloudflare-api-token \
  --from-file=api-token="$tmp/api-token" --dry-run=client -o yaml \
  | kubectl --context homelab apply -f - >/dev/null
echo "Monitoring DNS-01 token installed"
