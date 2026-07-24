#!/usr/bin/env bash
set -euo pipefail

grep -q '^status-alloy:' justfile
grep -q 'kubectl --context {{ctx}} -n monitoring get pods -l app.kubernetes.io/instance=alloy-metrics -o wide' justfile
grep -q 'kubectl --context {{ctx}} -n monitoring describe pods -l app.kubernetes.io/instance=alloy-metrics' justfile
grep -q 'kubectl --context {{ctx}} -n monitoring logs -l app.kubernetes.io/instance=alloy-metrics -c alloy --tail=100' justfile
grep -q '^restart-alloy:' justfile
grep -q 'kubectl --context {{ctx}} -n monitoring rollout restart deployment/alloy-metrics' justfile
grep -q 'kubectl --context {{ctx}} -n monitoring rollout status deployment/alloy-metrics --timeout=2m' justfile
