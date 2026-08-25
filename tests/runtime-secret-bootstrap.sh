#!/usr/bin/env bash
set -euo pipefail

script=scripts/bootstrap-runtime-secrets.sh
test -x "$script"

for path in \
  home-services/immich \
  home-services/karakeep \
  lab/langfuse \
  platform/healthchecks \
  platform/monitoring \
  platform/scrutiny \
  platform/wazuh/indexer-certs \
  platform/wazuh/dashboard-certs \
  platform/wazuh/indexer-security \
  platform/wazuh/wazuh-api-cred \
  platform/wazuh/wazuh-authd-pass \
  platform/wazuh/wazuh-cluster-key \
  platform/wazuh/dashboard-cred \
  platform/wazuh/indexer-cred; do
  grep -Fq "$path" "$script"
done

for key in \
  DB_PASSWORD NEXTAUTH_SECRET MEILI_MASTER_KEY \
  ENCRYPTION_KEY SALT POSTGRES_PASSWORD REDIS_PASSWORD CLICKHOUSE_PASSWORD \
  S3_ACCESS_KEY S3_SECRET_KEY SEAWEEDFS_S3_CONFIG \
  SECRET_KEY SUPERUSER_PASSWORD \
  DOCKER_INFLUXDB_INIT_ADMIN_TOKEN SCRUTINY_WEB_INFLUXDB_TOKEN \
  GRAFANA_ADMIN_PASSWORD GRAFANA_SECRET_KEY WEBHOOK_GRAFANA_ALERTS_SECRET \
  node.pem node-key.pem root-ca.pem admin.pem admin-key.pem \
  filebeat.pem filebeat-key.pem cert.pem key.pem internal_users.yml authd.pass; do
  grep -Fq "$key" "$script"
done

grep -Fq 'secret/data/shared/discord' "$script"
grep -Fq 'read -r bao_token || [ -n "$bao_token" ]' "$script"
grep -Fq 'mktemp -d' "$script"
grep -Fq 'shred -u' "$script"
grep -Fq 'openssl rand' "$script"
grep -Fq 'mkpasswd -m bcrypt' "$script"
grep -Fq 'subjectAltName=DNS:indexer' "$script"
grep -Fq 'CN=indexer/O=Company/L=California/C=US' "$script"
grep -Fq 'CN=admin/O=Company/L=California/C=US' "$script"
grep -Fq 'BAO_SSH' "$script"
grep -Fq 'PATH=/run/current-system/sw/bin:/home/erik/.nix-profile/bin:$PATH' "$script"
grep -Fq 'vault_key=$(jq' "$script"
! grep -Fq 'path=$(jq' "$script"
! grep -Fq 'set -x' "$script"

grep -Fq 'scripts/bootstrap-runtime-secrets.sh' justfile
