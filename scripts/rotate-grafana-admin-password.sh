#!/usr/bin/env bash
set -euo pipefail

BAO_SSH="${BAO_SSH:-discovery}"
BAO_SSH_PORT="${BAO_SSH_PORT:-2222}"
KUBE_CONTEXT="${KUBE_CONTEXT:-homelab}"

IFS= read -r bao_token || [ -n "$bao_token" ]
[ -n "$bao_token" ] || { echo "OpenBao operator token required on stdin" >&2; exit 2; }

tmp=$(mktemp -d)
chmod 700 "$tmp"
cleanup() {
  find "$tmp" -type f -exec shred -u {} + 2>/dev/null || true
  rmdir "$tmp" 2>/dev/null || true
}
trap cleanup EXIT

openssl rand -hex 24 >"$tmp/password"
before=$(kubectl --context "$KUBE_CONTEXT" -n monitoring get secret monitoring-secrets \
  -o jsonpath='{.data.GRAFANA_ADMIN_PASSWORD}' | sha256sum | cut -d' ' -f1)

{
  printf '%s\n' "$bao_token"
  cat "$tmp/password"
} | ssh -p "$BAO_SSH_PORT" -o BatchMode=yes "$BAO_SSH" '
  set -euo pipefail
  IFS= read -r token || [ -n "$token" ]
  IFS= read -r password || [ -n "$password" ]
  tmp=$(mktemp -d)
  chmod 700 "$tmp"
  cleanup() {
    find "$tmp" -type f -exec shred -u {} + 2>/dev/null || true
    rmdir "$tmp" 2>/dev/null || true
  }
  trap cleanup EXIT

  printf "X-Vault-Token: %s\n" "$token" >"$tmp/header"
  chmod 600 "$tmp/header"
  unset token
  curl --connect-timeout 5 --max-time 30 -fsS --header @"$tmp/header" \
    http://127.0.0.1:8200/v1/secret/data/platform/monitoring >"$tmp/current"
  jq -e ".data.data | type == \"object\"" "$tmp/current" >/dev/null
  jq -c --arg password "$password" \
    '\''{data:(.data.data + {GRAFANA_ADMIN_PASSWORD:$password})}'\'' \
    "$tmp/current" >"$tmp/request"
  unset password
  curl --connect-timeout 5 --max-time 30 -fsS --request POST \
    --header @"$tmp/header" --header "Content-Type: application/json" \
    --data-binary @"$tmp/request" \
    http://127.0.0.1:8200/v1/secret/data/platform/monitoring >/dev/null
'
unset bao_token

kubectl --context "$KUBE_CONTEXT" -n monitoring annotate externalsecret monitoring \
  force-sync="$(date +%s)" --overwrite >/dev/null

rotated=false
for _ in {1..30}; do
  after=$(kubectl --context "$KUBE_CONTEXT" -n monitoring get secret monitoring-secrets \
    -o jsonpath='{.data.GRAFANA_ADMIN_PASSWORD}' | sha256sum | cut -d' ' -f1)
  if [ "$after" != "$before" ]; then
    rotated=true
    break
  fi
  sleep 2
done
"$rotated"

kubectl --context "$KUBE_CONTEXT" -n monitoring rollout restart deployment/monitoring-grafana >/dev/null
kubectl --context "$KUBE_CONTEXT" -n monitoring rollout status deployment/monitoring-grafana --timeout=3m
kubectl --context "$KUBE_CONTEXT" -n monitoring exec deployment/monitoring-grafana -c grafana -- \
  sh -ceu 'printf "%s\n" "$GF_SECURITY_ADMIN_PASSWORD" | grafana cli admin reset-admin-password --password-from-stdin >/dev/null'

printf 'user = "admin:%s"\n' "$(<"$tmp/password")" >"$tmp/curl-config"
chmod 600 "$tmp/curl-config"
curl --config "$tmp/curl-config" --fail --silent --show-error \
  --retry 12 --retry-all-errors --retry-delay 5 \
  https://grafana.homelab.pastelariadev.com/api/user >/dev/null

echo "Grafana admin password rotated in OpenBao, reconciled by ESO, and verified"
