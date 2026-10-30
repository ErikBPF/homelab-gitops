#!/usr/bin/env bash
set -euo pipefail

values=platform/alloy-metrics/values.yaml

grep -q 'memory: 128Mi' "$values"
grep -q 'memory: 512Mi' "$values"
grep -q 'sample_limit.*=.*15000' "$values"
grep -q 'sample_limit.*=.*25000' "$values"
grep -q 'path: /-/ready' "$values"
grep -q 'sample_age_limit = "30m"' "$values"
grep -q 'prometheus.scrape "alloy"' "$values"
grep -q 'prometheus_remote_storage_samples_pending' "$values"
grep -q 'prometheus_remote_storage_samples_retried_total' "$values"
grep -q 'http://monitoring-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090/api/v1/write' "$values"
! grep -q '100.103.52.100:9090' "$values"

python3 - "$values" <<'PY'
import pathlib, re, sys
import yaml

config = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text())["alloy"]["alloy"]["configMap"]["content"]
for scrape in ["cadvisor", "kubelet"]:
    blocks = re.findall(rf'^prometheus\.scrape "{scrape}" \{{\n(.*?)^\}}', config, re.M | re.S)
    assert len(blocks) == 1, f"expected one {scrape} scrape"
    body = blocks[0]
    assert not re.search(r'insecure_skip_verify\s*=\s*true', body), f"{scrape}: server verification disabled"
    tls = re.search(r'tls_config\s*\{([^}]*)\}', body, re.S)
    assert tls, f"{scrape}: missing TLS configuration"
    assert re.search(r'ca_file\s*=\s*"/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"', tls[1]), f"{scrape}: missing projected API CA"
    assert re.search(r'server_name\s*=\s*"kubernetes.default.svc"', tls[1]), f"{scrape}: wrong API server identity"

block = pathlib.Path(sys.argv[1]).read_text().split('prometheus.relabel "etcd_keep" {', 1)[1]
pattern = re.search(r'regex\s*=\s*"([^"]+)"', block).group(1)
for metric in ['up', 'etcd_server_has_leader', 'etcd_server_proposals_applied_total',
               'etcd_server_proposals_committed_total', 'etcd_server_proposals_failed_total',
               'etcd_server_proposals_pending']:
    assert re.fullmatch(pattern, metric), f"etcd relabel filter drops {metric}"
assert not re.fullmatch(pattern, 'unrelated_unused_metric')
PY

python3 tests/alloy-rbac.py
