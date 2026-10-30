#!/usr/bin/env bash
set -euo pipefail

dry_run=()
if [[ ${1:-} == --dry-run ]]; then
  dry_run=(--dry-run)
  shift
fi
if [[ $# != 1 || ! -f $1 ]]; then
  echo 'usage: run.sh [--dry-run] pinned-alloy.oci.tar' >&2
  exit 2
fi
root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
archive=$(realpath -- "$1")
temporary=$(mktemp -d -t alloy-replica-r21.XXXXXXXX)
trap 'rm -rf -- "$temporary"' EXIT
mkdir -m 0700 "$temporary/home"
cp -- "$archive" "$temporary/alloy-replica.oci.tar"
expected=$(python3 -c 'import json,pathlib,sys; print(json.loads(pathlib.Path(sys.argv[1]).read_text())["image_archive_sha256"])' "$root/tests/replica/artifacts.json")
actual=$(sha256sum "$temporary/alloy-replica.oci.tar")
if [[ ${actual%% *} != "$expected" ]]; then
  echo 'refusing unreviewed Alloy archive' >&2
  exit 2
fi
run_id=$(python3 -c 'import uuid; print(uuid.uuid4())')
clean=(env -i "HOME=$temporary/home" "PATH=$PATH" NIX_PATH=)
stored=$("${clean[@]}" nix store add-file --store daemon --option substituters https://cache.nixos.org --option extra-substituters '' --option accept-flake-config false "$temporary/alloy-replica.oci.tar")
case "$stored" in
  /nix/store/*-alloy-replica.oci.tar) ;;
  *) echo 'unexpected local archive store path' >&2; exit 2 ;;
esac
command=(nix build --store daemon --option substituters https://cache.nixos.org --option extra-substituters '' --option accept-flake-config false --option sandbox true --option builders '' --max-jobs 2 --cores 2 --no-link --print-out-paths
  --file "$root/tests/replica/alloy-security.nix" --argstr runId "$run_id" --arg imageArchive "$stored")
if [[ ${#dry_run[@]} == 1 ]]; then
  "${clean[@]}" "${command[@]}" "${dry_run[@]}"
  exit
fi
output=$("${clean[@]}" "${command[@]}")
python3 - "$output/alloy-replica.json" "$run_id" <<'PY'
import json, sys
receipt = json.load(open(sys.argv[1]))
if receipt.get("run_id") != sys.argv[2] or receipt.get("cleanup") != "both guests stopped":
    raise SystemExit("replica run or cleanup receipt mismatch")
print("ALLOY_REPLICA_PERMISSIONS_METRICS_OK")
print("receipt=" + sys.argv[1])
PY
