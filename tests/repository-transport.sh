#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
expected='ssh://git@ssh.github.com:443/ErikBPF/homelab-gitops.git'
sources=$(grep -Rh 'repoURL:.*homelab-gitops' \
  "$repo_root/bootstrap" "$repo_root/clusters")

if [ -z "$sources" ] || printf '%s\n' "$sources" | grep -Fv "$expected"; then
  echo "homelab-gitops sources must use GitHub SSH over port 443" >&2
  exit 1
fi

if ! grep -Fq "$expected" "$repo_root/justfile"; then
  echo "set-repo must replace the canonical port-443 URL" >&2
  exit 1
fi
