#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

cat >"$tmp/valid.yaml" <<'YAML'
apiVersion: apps/v1
kind: Deployment
metadata: {name: valid}
spec:
  template:
    spec:
      containers:
        - name: app
          image: example.invalid/app@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
          resources:
            requests: {cpu: 100m, memory: 128Mi}
            limits: {cpu: 500m, memory: 512Mi}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: data}
spec:
  storageClassName: local-path
  resources:
    requests: {storage: 1Gi}
YAML

python "$root/scripts/check-first-wave-resources.py" "$tmp/valid.yaml"

sed '/limits:/d' "$tmp/valid.yaml" >"$tmp/unbounded.yaml"
if python "$root/scripts/check-first-wave-resources.py" "$tmp/unbounded.yaml" >/dev/null 2>&1; then
  echo "container without limits unexpectedly accepted" >&2
  exit 1
fi

sed '/storageClassName:/d' "$tmp/valid.yaml" >"$tmp/unclassified-pvc.yaml"
if python "$root/scripts/check-first-wave-resources.py" "$tmp/unclassified-pvc.yaml" >/dev/null 2>&1; then
  echo "PVC without storage class unexpectedly accepted" >&2
  exit 1
fi

sed 's/memory: 512Mi/memory: 512Mi, nvidia.com\/gpu: 1/' "$tmp/valid.yaml" >"$tmp/gpu.yaml"
if python "$root/scripts/check-first-wave-resources.py" --forbid-gpu "$tmp/gpu.yaml" >/dev/null 2>&1; then
  echo "GPU resource unexpectedly accepted" >&2
  exit 1
fi

grep -Eq '^capacity:' "$root/justfile"
grep -Fq 'kubectl --context "$context" top nodes' "$root/scripts/capacity.sh"
grep -Fq 'ssh -p 2222' "$root/scripts/capacity.sh"
