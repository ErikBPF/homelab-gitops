#!/usr/bin/env bash
set -euo pipefail

script=scripts/rotate-grafana-admin-password.sh
test -x "$script"

grep -Fq 'read -r bao_token || [ -n "$bao_token" ]' "$script"
grep -Fq 'secret/data/platform/monitoring' "$script"
grep -Fq 'GRAFANA_ADMIN_PASSWORD' "$script"
grep -Fq 'force-sync=' "$script"
grep -Fq 'rollout restart deployment/monitoring-grafana' "$script"
grep -Fq 'reset-admin-password --password-from-stdin' "$script"
grep -Fq -- '--retry-all-errors' "$script"
grep -Fq 'shred -u' "$script"
! grep -Fq 'set -x' "$script"

grep -Fq 'scripts/rotate-grafana-admin-password.sh' justfile
