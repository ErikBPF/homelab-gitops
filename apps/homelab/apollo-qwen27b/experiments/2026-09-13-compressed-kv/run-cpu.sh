#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
ulimit -v 524288
"$1" -B -m unittest -v test_reference
exec "$1" -B reference.py
