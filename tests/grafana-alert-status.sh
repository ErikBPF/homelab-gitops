#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
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
grep -Fq 'header = "Authorization: Basic YWRtaW46dGVzdC1wYXNzd29yZA=="' "$config"
if [ "${FAIL_CURL:-0}" = 1 ]; then exit 22; fi
printf '%s\n' "$ALERT_FIXTURE"
EOF
chmod +x "$tmp/kubectl" "$tmp/curl"

export ALERT_FIXTURE='[{"status":{"state":"active"},"startsAt":"2026-01-01T00:00:00.000Z","labels":{"severity":"warning","alertname":"systemd unit failed","instance":"kepler","name":"nix-gc.service","mountpoint":"/","source_ip":"192.0.2.1"},"annotations":{"summary":"secret-shaped detail"}}]'
output=$(PATH="$tmp:$PATH" "$root/scripts/grafana-alert-status.sh")
grep -Fxq 'active=1' <<<"$output"
grep -Fq $'active\twarning\tsystemd unit failed\tkepler\tnix-gc.service\t-\t/' <<<"$output"
if grep -Fq '192.0.2.1' <<<"$output"; then exit 1; fi
if grep -Fq 'secret-shaped detail' <<<"$output"; then exit 1; fi
if grep -Fq 'test-password' <<<"$output"; then exit 1; fi
grep -Fq $'state\tseverity\talert\ttarget\tcomponent\tnamespace\tmount\tsince_utc\tage_minutes' <<<"$output"
grep -Fq $'2026-01-01T00:00:00.000Z\t' <<<"$output"
awk -F '\t' 'NR == 3 { if ($9 !~ /^[0-9]+$/ || $9 <= 0) exit 1 }' <<<"$output"

export ALERT_FIXTURE='[{"status":{"state":"active"},"labels":{"instance":"kepler","backup":"offsite","alertname":"backup stale"}}]'
output=$(PATH="$tmp:$PATH" "$root/scripts/grafana-alert-status.sh")
grep -Fq $'kepler\toffsite\t-\t-\t-' <<<"$output"

export ALERT_FIXTURE='[{"labels":{"cluster":"homelab","namespace":"monitoring","pod":"alloy-test","alertname":"collector unready"}}]'
output=$(PATH="$tmp:$PATH" "$root/scripts/grafana-alert-status.sh")
grep -Fq $'homelab\talloy-test\tmonitoring\t-' <<<"$output"

export ALERT_FIXTURE='[]'
output=$(PATH="$tmp:$PATH" "$root/scripts/grafana-alert-status.sh")
grep -Fxq 'active=0' <<<"$output"

for fixture in \
  '{"message":"private upstream error"}' \
  '[{"labels":"private malformed labels"}]' \
  '[{"labels":{"instance":{"private":"SHOULD_NOT_PRINT"}}}]' \
  '[{"labels":{},"status":{"state":{"private":"SHOULD_NOT_PRINT"}}}]' \
  '[{"labels":{},"startsAt":"private timestamp"}]'; do
  if failure=$(ALERT_FIXTURE="$fixture" PATH="$tmp:$PATH" "$root/scripts/grafana-alert-status.sh" 2>&1); then
    echo 'Malformed Grafana response was reported as success' >&2
    exit 1
  fi
  if grep -Fq 'active=' <<<"$failure"; then exit 1; fi
  if grep -Fq 'private' <<<"$failure"; then exit 1; fi
done

if failure=$(FAIL_CURL=1 PATH="$tmp:$PATH" "$root/scripts/grafana-alert-status.sh" 2>&1); then
  echo 'Grafana failure was reported as success' >&2
  exit 1
fi
if grep -Fq 'active=0' <<<"$failure"; then exit 1; fi

grep -Fq 'grafana-alert-status:' "$root/justfile"
grep -Fq 'scripts/grafana-alert-status.sh' "$root/justfile"
