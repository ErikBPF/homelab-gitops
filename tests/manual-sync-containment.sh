#!/usr/bin/env bash
set -euo pipefail

if rg -n '^\s+automated:' bootstrap clusters; then
  echo "automated Argo sync is forbidden while main is unprotected" >&2
  exit 1
fi
