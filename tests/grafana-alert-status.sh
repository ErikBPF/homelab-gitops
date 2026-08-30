#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

cat >"$tmp/kubectl" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  *GRAFANA_ADMIN_USER*) printf '%s' 'YWRtaW4=' ;;
  *GRAFANA_ADMIN_PASSWORD*) printf '%s' 'dGVzdC1wYXNzd29yZA==' ;;
  *) exit 2 ;;
esac
EOF

cat >"$tmp/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "$*" in
  *admin*|*test-password*) echo 'credential leaked in curl arguments' >&2; exit 3 ;;
esac
config=
while [ "$#" -gt 0 ]; do
  if [ "$1" = --config ]; then config=$2; shift 2; else shift; fi
done
test -n "$config"
test -p "$config"
grep -Fq 'user = "admin:test-password"' "$config"
if [ "${FAIL_CURL:-0}" = 1 ]; then exit 22; fi
printf '%s\n' "$ALERT_FIXTURE"
EOF
chmod +x "$tmp/kubectl" "$tmp/curl"

export ALERT_FIXTURE='[{"status":{"state":"active"},"labels":{"severity":"warning","alertname":"systemd unit failed","instance":"kepler","name":"nix-gc.service","mountpoint":"/","source_ip":"192.0.2.1"},"annotations":{"summary":"secret-shaped detail"}}]'
output=$(PATH="$tmp:$PATH" "$root/scripts/grafana-alert-status.sh")
grep -Fxq 'active=1' <<<"$output"
grep -Fq $'active\twarning\tsystemd unit failed\tkepler\tnix-gc.service\t/' <<<"$output"
! grep -Fq '192.0.2.1' <<<"$output"
! grep -Fq 'secret-shaped detail' <<<"$output"
! grep -Fq 'test-password' <<<"$output"

if failure=$(FAIL_CURL=1 PATH="$tmp:$PATH" "$root/scripts/grafana-alert-status.sh" 2>&1); then
  echo 'Grafana failure was reported as success' >&2
  exit 1
fi
! grep -Fq 'active=0' <<<"$failure"

grep -Fq 'grafana-alert-status:' "$root/justfile"
grep -Fq 'scripts/grafana-alert-status.sh' "$root/justfile"
