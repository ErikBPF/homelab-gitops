#!/usr/bin/env bash
set -euo pipefail

BAO_SSH="${BAO_SSH:-192.168.10.210}"
BAO_SSH_PORT="${BAO_SSH_PORT:-2222}"

IFS= read -r bao_token || [ -n "$bao_token" ]
[ -n "$bao_token" ] || { echo "OpenBao operator token required on stdin" >&2; exit 2; }

# shellcheck disable=SC2317,SC2329 # Serialized and invoked by the remote shell.
remote_main() (
  local token=$1 dir robot_name robot_id password payload auth dockerconfig code http_status
  dir=$(mktemp -d)
  chmod 700 "$dir"
  cleanup() {
    find "$dir" -type f -exec shred -u {} + 2>/dev/null || true
    rmdir "$dir" 2>/dev/null || true
  }
  trap cleanup EXIT
  printf 'X-Vault-Token: %s\n' "$token" >"$dir/header"
  chmod 600 "$dir/header"
  unset token

  # shellcheck disable=SC2016 # Literal Harbor robot prefix.
  robot_name='robot$library+cognee'
  # Harbor 2.15.2 returns project robots by ID but omits them from /robots.
  robot_id=$(docker exec harbor-db psql -U postgres -d registry -Atc \
    "select id from robot where name='library+cognee' and disabled=false")
  if [ -n "$robot_id" ]; then
    password="Aa1$(openssl rand -hex 30)"
    jq -cn --arg secret "$password" '{secret:$secret}' \
      | docker exec -i harbor-core sh -c \
        'curl -fsS -X PATCH -u "admin:$HARBOR_ADMIN_PASSWORD" -H "Content-Type: application/json" --data-binary @- "http://127.0.0.1:8080/api/v2.0/robots/$1"' \
        sh "$robot_id" >/dev/null
  else
    payload='{"name":"cognee","description":"Cognee publisher and Kepler pull canary","level":"project","duration":-1,"permissions":[{"kind":"project","namespace":"library","access":[{"resource":"repository","action":"pull"},{"resource":"repository","action":"push"}]}]}'
    docker exec harbor-core sh -c \
      'curl -fsS -u "admin:$HARBOR_ADMIN_PASSWORD" -H "Content-Type: application/json" --data "$1" http://127.0.0.1:8080/api/v2.0/robots' \
      sh "$payload" >"$dir/robot"
    password=$(jq -er '.secret | select(length > 20)' "$dir/robot")
  fi

  auth=$(printf '%s:%s' "$robot_name" "$password" | base64 -w0)
  dockerconfig=$(jq -cn \
    --arg host harbor.homelab.pastelariadev.com \
    --arg username "$robot_name" --arg password "$password" --arg auth "$auth" \
    '{auths:{($host):{username:$username,password:$password,auth:$auth}}}')

  curl -fsS --header @"$dir/header" \
    http://127.0.0.1:8200/v1/secret/data/home/harbor >"$dir/current"
  jq -c --arg username "$robot_name" --arg password "$password" \
    '{data:(.data.data + {HARBOR_ROBOT_USER:$username,HARBOR_ROBOT_SECRET:$password})}' \
    "$dir/current" >"$dir/request"
  curl -fsS -X POST --header @"$dir/header" -H 'Content-Type: application/json' \
    --data-binary @"$dir/request" http://127.0.0.1:8200/v1/secret/data/home/harbor >/dev/null

  http_status=$(curl -sS -o "$dir/current" -w '%{http_code}' --header @"$dir/header" \
    http://127.0.0.1:8200/v1/secret/data/lab/cognee)
  case "$http_status" in
    200) jq -e '.data.data | type == "object"' "$dir/current" >/dev/null ;;
    404) printf '{"data":{"data":{}}}\n' >"$dir/current" ;;
    *) return 1 ;;
  esac
  jq -c \
    --arg pg "$(openssl rand -hex 24)" --arg jwt "$(openssl rand -hex 32)" \
    --arg verify "$(openssl rand -hex 32)" --arg reset "$(openssl rand -hex 32)" \
    --arg ingest "$(openssl rand -hex 32)" --arg dockerconfig "$dockerconfig" \
    '{data:(.data.data + {
      POSTGRES_PASSWORD:(.data.data.POSTGRES_PASSWORD // $pg),
      FASTAPI_USERS_JWT_SECRET:(.data.data.FASTAPI_USERS_JWT_SECRET // $jwt),
      FASTAPI_USERS_VERIFICATION_TOKEN_SECRET:(.data.data.FASTAPI_USERS_VERIFICATION_TOKEN_SECRET // $verify),
      FASTAPI_USERS_RESET_PASSWORD_TOKEN_SECRET:(.data.data.FASTAPI_USERS_RESET_PASSWORD_TOKEN_SECRET // $reset),
      COGNEE_INGEST_PASSWORD:(.data.data.COGNEE_INGEST_PASSWORD // $ingest),
      HARBOR_DOCKERCONFIGJSON:$dockerconfig
    })}' "$dir/current" >"$dir/request"
  curl -fsS -X POST --header @"$dir/header" -H 'Content-Type: application/json' \
    --data-binary @"$dir/request" http://127.0.0.1:8200/v1/secret/data/lab/cognee >/dev/null

  code=$(curl -sS -o "$dir/token-response" -w '%{http_code}' -u "$robot_name:$password" \
    'http://127.0.0.1:8085/service/token?service=harbor-registry&scope=repository:library/cognee-homelab:pull,push')
  [ "$code" = 200 ]
  jq -e '.token | length > 20' "$dir/token-response" >/dev/null
  unset password auth dockerconfig
)

{
  printf '%s\n' "$bao_token"
  declare -f remote_main
  # shellcheck disable=SC2016 # Expanded by the remote shell.
  printf '%s\n' 'remote_main "$token"'
} | ssh -p "$BAO_SSH_PORT" -o BatchMode=yes "$BAO_SSH" \
  "bash -c 'set -euo pipefail; IFS= read -r token || [ -n \"\$token\" ]; . /dev/stdin'"

unset bao_token
echo "Cognee Harbor robot and runtime values stored in OpenBao"
