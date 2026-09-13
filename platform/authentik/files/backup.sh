#!/usr/bin/env bash
set -euo pipefail
umask 027

root=${1:-/backups}
# A killed pod may leave an unpublished directory; no live job can be a day old.
find "$root" -mindepth 1 -maxdepth 1 -type d -name '.incomplete.*' -mtime +1 -exec rm -rf -- {} +
started=$(date -u +%s)
stamp=$(date -u -d "@$started" +%Y%m%dT%H%M%SZ)
temporary=$(mktemp -d "$root/.incomplete.XXXXXX")
trap 'rm -rf -- "$temporary"' EXIT
printf '%s\n' "$started" >"$temporary/created-at"
pg_dump --format=custom --file="$temporary/postgres.dump"
test -s "$temporary/postgres.dump"
pg_restore --list "$temporary/postgres.dump" >/dev/null
(
  cd "$temporary"
  sha256sum postgres.dump created-at >SHA256SUMS
)
chmod 0640 "$temporary"/*
test ! -e "$root/$stamp"
mv -T -- "$temporary" "$root/$stamp"
# Only this producer's completed timestamp directories are eligible for retention.
find "$root" -regextype posix-extended -mindepth 1 -maxdepth 1 -type d \
  -regex '.*/[0-9]{8}T[0-9]{6}Z' -mtime +14 -exec rm -rf -- {} +
printf 'Authentik dump completed: %s\n' "$stamp"
