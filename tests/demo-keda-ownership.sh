#!/usr/bin/env bash
set -euo pipefail

if grep -q '^[[:space:]]*replicas:' apps/demo/deployment.yaml; then
  echo "KEDA-managed demo Deployment must not declare spec.replicas" >&2
  exit 1
fi
