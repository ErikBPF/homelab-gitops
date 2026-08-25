#!/usr/bin/env bash
set -euo pipefail

BAO_SSH="${BAO_SSH:-discovery}"
BAO_SSH_PORT="${BAO_SSH_PORT:-2222}"

IFS= read -r bao_token || [ -n "$bao_token" ]
[ -n "$bao_token" ] || { echo "OpenBao operator token required on stdin" >&2; exit 2; }

tmp=$(mktemp -d)
chmod 700 "$tmp"
cleanup() {
  find "$tmp" -type f -exec shred -u {} + 2>/dev/null || true
  rmdir "$tmp" 2>/dev/null || true
}
trap cleanup EXIT

random_hex() { openssl rand -hex "$2" >"$tmp/$1"; }
random_b64() { openssl rand -base64 "$2" >"$tmp/$1"; }

random_hex immich-db 24
random_b64 karakeep-nextauth 32
random_hex karakeep-meili 32
random_b64 langfuse-salt 32
random_hex langfuse-encryption 32
random_b64 langfuse-nextauth 32
random_hex langfuse-postgres 32
random_hex langfuse-redis 32
random_hex langfuse-clickhouse 32
random_hex langfuse-s3-access 10
random_hex langfuse-s3-secret 20
random_hex healthchecks-key 32
random_hex healthchecks-password 24
random_hex scrutiny-password 24
random_hex scrutiny-token 32
random_hex grafana-password 24
random_hex grafana-key 32
random_hex wazuh-indexer-password 24
random_hex wazuh-dashboard-password 24
random_hex wazuh-api-password 24
random_hex wazuh-authd 24
random_hex wazuh-cluster 16

mkpasswd -m bcrypt -s <"$tmp/wazuh-indexer-password" >"$tmp/wazuh-admin-hash"
mkpasswd -m bcrypt -s <"$tmp/wazuh-dashboard-password" >"$tmp/wazuh-dashboard-hash"
printf '%s\n' \
  '_meta:' \
  '  type: "internalusers"' \
  '  config_version: 2' \
  'admin:' \
  "  hash: \"$(<"$tmp/wazuh-admin-hash")\"" \
  '  reserved: true' \
  '  backend_roles: ["admin"]' \
  'kibanaserver:' \
  "  hash: \"$(<"$tmp/wazuh-dashboard-hash")\"" \
  '  reserved: true' >"$tmp/internal_users.yml"

openssl req -x509 -newkey rsa:2048 -nodes -days 825 -sha256 \
  -subj '/CN=Wazuh Root CA/O=Company/L=California/C=US' \
  -keyout "$tmp/root-ca.key" -out "$tmp/root-ca.pem" >/dev/null 2>&1

sign_cert() {
  local name=$1 subject=$2 extensions=$3
  openssl req -newkey rsa:2048 -nodes -sha256 -subj "$subject" \
    -keyout "$tmp/$name-key.pem" -out "$tmp/$name.csr" >/dev/null 2>&1
  printf '%s\n' "$extensions" >"$tmp/$name.ext"
  openssl x509 -req -in "$tmp/$name.csr" -CA "$tmp/root-ca.pem" -CAkey "$tmp/root-ca.key" \
    -CAcreateserial -days 825 -sha256 -extfile "$tmp/$name.ext" -out "$tmp/$name.pem" >/dev/null 2>&1
}

sign_cert indexer '/CN=indexer/O=Company/L=California/C=US' \
  'subjectAltName=DNS:indexer,DNS:wazuh-indexer,DNS:wazuh-indexer-0,DNS:wazuh-indexer-0.wazuh-indexer,DNS:wazuh-indexer-0.wazuh-indexer.wazuh.svc,DNS:wazuh-indexer-0.wazuh-indexer.wazuh.svc.cluster.local'
sign_cert admin '/CN=admin/O=Company/L=California/C=US' 'extendedKeyUsage=clientAuth'
sign_cert filebeat '/CN=filebeat/O=Company/L=California/C=US' 'extendedKeyUsage=clientAuth'
sign_cert dashboard '/CN=dashboard/O=Company/L=California/C=US' \
  'subjectAltName=DNS:dashboard,DNS:wazuh-dashboard,DNS:wazuh-dashboard.wazuh.svc,DNS:wazuh.homelab.pastelariadev.com'

payload=$(jq -nc \
  --rawfile immich_db "$tmp/immich-db" \
  --rawfile karakeep_nextauth "$tmp/karakeep-nextauth" \
  --rawfile karakeep_meili "$tmp/karakeep-meili" \
  --rawfile langfuse_salt "$tmp/langfuse-salt" \
  --rawfile langfuse_encryption "$tmp/langfuse-encryption" \
  --rawfile langfuse_nextauth "$tmp/langfuse-nextauth" \
  --rawfile langfuse_postgres "$tmp/langfuse-postgres" \
  --rawfile langfuse_redis "$tmp/langfuse-redis" \
  --rawfile langfuse_clickhouse "$tmp/langfuse-clickhouse" \
  --rawfile s3_access "$tmp/langfuse-s3-access" \
  --rawfile s3_secret "$tmp/langfuse-s3-secret" \
  --rawfile healthchecks_key "$tmp/healthchecks-key" \
  --rawfile healthchecks_password "$tmp/healthchecks-password" \
  --rawfile scrutiny_password "$tmp/scrutiny-password" \
  --rawfile scrutiny_token "$tmp/scrutiny-token" \
  --rawfile grafana_password "$tmp/grafana-password" \
  --rawfile grafana_key "$tmp/grafana-key" \
  --rawfile indexer_password "$tmp/wazuh-indexer-password" \
  --rawfile dashboard_password "$tmp/wazuh-dashboard-password" \
  --rawfile api_password "$tmp/wazuh-api-password" \
  --rawfile authd "$tmp/wazuh-authd" \
  --rawfile cluster_key "$tmp/wazuh-cluster" \
  --rawfile root_ca "$tmp/root-ca.pem" \
  --rawfile indexer_cert "$tmp/indexer.pem" \
  --rawfile indexer_key "$tmp/indexer-key.pem" \
  --rawfile admin_cert "$tmp/admin.pem" \
  --rawfile admin_key "$tmp/admin-key.pem" \
  --rawfile filebeat_cert "$tmp/filebeat.pem" \
  --rawfile filebeat_key "$tmp/filebeat-key.pem" \
  --rawfile dashboard_cert "$tmp/dashboard.pem" \
  --rawfile dashboard_key "$tmp/dashboard-key.pem" \
  --rawfile internal_users "$tmp/internal_users.yml" \
  'def trim: sub("\\n$"; "");
   {secrets:[
    {path:"home-services/immich",data:{DB_PASSWORD:($immich_db|trim)}},
    {path:"home-services/karakeep",data:{NEXTAUTH_SECRET:($karakeep_nextauth|trim),MEILI_MASTER_KEY:($karakeep_meili|trim)}},
    {path:"lab/langfuse",data:{
      SALT:($langfuse_salt|trim),ENCRYPTION_KEY:($langfuse_encryption|trim),NEXTAUTH_SECRET:($langfuse_nextauth|trim),
      POSTGRES_USER:"postgres",POSTGRES_PASSWORD:($langfuse_postgres|trim),POSTGRES_DB:"langfuse",USERDB_USER:"langfuse",
      REDIS_PASSWORD:($langfuse_redis|trim),CLICKHOUSE_PASSWORD:($langfuse_clickhouse|trim),
      S3_ACCESS_KEY:($s3_access|trim),S3_SECRET_KEY:($s3_secret|trim),
      SEAWEEDFS_S3_CONFIG:({identities:[{name:"langfuse",credentials:[{accessKey:($s3_access|trim),secretKey:($s3_secret|trim)}],actions:["Admin","Read","Write"]}]}|tojson)
    }},
    {path:"platform/healthchecks",data:{SECRET_KEY:($healthchecks_key|trim),SUPERUSER_EMAIL:"admin@homelab.local",SUPERUSER_PASSWORD:($healthchecks_password|trim)}},
    {path:"platform/monitoring",data:{GRAFANA_ADMIN_USER:"admin",GRAFANA_ADMIN_PASSWORD:($grafana_password|trim),GRAFANA_SECRET_KEY:($grafana_key|trim)}},
    {path:"platform/scrutiny",data:{DOCKER_INFLUXDB_INIT_PASSWORD:($scrutiny_password|trim),DOCKER_INFLUXDB_INIT_ADMIN_TOKEN:($scrutiny_token|trim),SCRUTINY_WEB_INFLUXDB_TOKEN:($scrutiny_token|trim)}},
    {path:"platform/wazuh/indexer-certs",data:{"node.pem":$indexer_cert,"node-key.pem":$indexer_key,"root-ca.pem":$root_ca,"admin.pem":$admin_cert,"admin-key.pem":$admin_key,"filebeat.pem":$filebeat_cert,"filebeat-key.pem":$filebeat_key}},
    {path:"platform/wazuh/dashboard-certs",data:{"cert.pem":$dashboard_cert,"key.pem":$dashboard_key,"root-ca.pem":$root_ca}},
    {path:"platform/wazuh/indexer-security",data:{"internal_users.yml":$internal_users}},
    {path:"platform/wazuh/wazuh-api-cred",data:{username:"wazuh-wui",password:($api_password|trim)}},
    {path:"platform/wazuh/wazuh-authd-pass",data:{"authd.pass":($authd|trim)}},
    {path:"platform/wazuh/wazuh-cluster-key",data:{key:($cluster_key|trim)}},
    {path:"platform/wazuh/dashboard-cred",data:{username:"kibanaserver",password:($dashboard_password|trim)}},
    {path:"platform/wazuh/indexer-cred",data:{username:"admin",password:($indexer_password|trim)}}
   ]}')

printf '%s\n%s\n' "$bao_token" "$payload" | ssh -p "$BAO_SSH_PORT" -o BatchMode=yes "$BAO_SSH" '
  set -euo pipefail
  export PATH=/run/current-system/sw/bin:/home/erik/.nix-profile/bin:$PATH
  IFS= read -r token || [ -n "$token" ]
  IFS= read -r payload || [ -n "$payload" ]
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
  printf "%s" "$payload" >"$tmp/payload"
  unset payload

  curl --connect-timeout 5 --max-time 30 -fsS --header @"$tmp/header" \
    http://127.0.0.1:8200/v1/secret/data/shared/discord >"$tmp/discord"
  jq -e ".data.data | .incidents and .deploys and .argus_webhook_hmac" "$tmp/discord" >/dev/null
  jq -c --slurpfile shared "$tmp/discord" '\''
    ($shared[0].data.data) as $discord |
    (.secrets[] | select(.path == "platform/monitoring").data) += {
      DISCORD_WEBHOOK_INCIDENTS:$discord.incidents,
      DISCORD_WEBHOOK_DEPLOYS:$discord.deploys,
      WEBHOOK_GRAFANA_ALERTS_SECRET:$discord.argus_webhook_hmac
    }
  '\'' "$tmp/payload" >"$tmp/complete"

  jq -c ".secrets[]" "$tmp/complete" | while IFS= read -r item; do
    vault_key=$(jq -er ".path" <<<"$item")
    [[ $vault_key =~ ^(home-services|lab|platform)/[A-Za-z0-9./-]+$ ]]
    jq -c "{data:.data}" <<<"$item" >"$tmp/request"
    curl --connect-timeout 5 --max-time 30 -fsS --request POST \
      --header @"$tmp/header" --header "Content-Type: application/json" \
      --data-binary @"$tmp/request" "http://127.0.0.1:8200/v1/secret/data/$vault_key" >/dev/null
  done
'

unset bao_token payload
echo "Kubernetes runtime secrets bootstrapped in Vault"
