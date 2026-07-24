#!/usr/bin/env bash
set -euo pipefail

values=platform/alloy-metrics/values.yaml

grep -q 'memory: 128Mi' "$values"
grep -q 'memory: 512Mi' "$values"
grep -q 'sample_limit.*=.*10000' "$values"
grep -q 'sample_limit.*=.*25000' "$values"
grep -q 'path: /-/ready' "$values"
grep -q 'sample_age_limit = "30m"' "$values"
grep -q 'prometheus.scrape "alloy"' "$values"
grep -q 'prometheus_remote_storage_samples_pending' "$values"
grep -q 'prometheus_remote_storage_samples_retries_total' "$values"
