#!/usr/bin/env python3
"""Evaluate provisioned alert expressions: promtool and PyYAML required.

Run: nix shell nixpkgs#prometheus.cli --command python3 tests/monitoring-promql.py
"""
from pathlib import Path
import subprocess
import tempfile

import yaml

root = Path(__file__).resolve().parents[1]
rules = yaml.safe_load((root / "platform/monitoring/files/alerting/rules.yaml").read_text())
rule = next(rule for group in rules["groups"] for rule in group["rules"]
            if rule["uid"] == "apollo-k3s-readiness")
expr = next(query["model"]["expr"] for query in rule["data"] if query["refId"] == "A")
tests = []
for name, ready, timestamp, expected in [
    ("healthy", "5", "0+60x10", 0),
    ("zero ready nodes", "0", "0+60x10", 1),
    ("four ready nodes", "4", "0+60x10", 1),
    ("stale successful probe", "5", "0", 1),
    ("missing probe", "5", None, 1),
    ("missing readiness", None, "0+60x10", 1),
    ("no metrics", None, None, 1),
]:
    series = []
    for metric, values in [("apollo_k3s_ready_nodes", ready),
                           ("apollo_k3s_probe_last_success_seconds", timestamp)]:
        if values is not None:
            series.append({"series": f'{metric}{{instance="apollo",job="integrations/unix"}}',
                           "values": values if "x" in values else f"{values}+0x10"})
    tests.append({
        "name": name, "interval": "1m", "input_series": series,
        "promql_expr_test": [{
            "expr": f"({expr}) > bool 0", "eval_time": "10m",
            "exp_samples": [{"labels": '{instance="apollo"}', "value": expected}],
        }],
    })

all_rules = {
    rule["uid"]: rule
    for path in (root / "platform/monitoring/files/alerting").glob("*rules.yaml")
    for group in yaml.safe_load(path.read_text())["groups"]
    for rule in group["rules"]
}


def alert_case(uid, name, metrics, expected):
    """Apply the rule's real threshold and NoData policy after instant reduction."""
    rule = all_rules[uid]
    query = next(item["model"]["expr"] for item in rule["data"] if item["refId"] == "A")
    condition = next(item["model"] for item in rule["data"] if item["refId"] == rule["condition"])
    evaluator = condition["conditions"][0]["evaluator"]
    operator = {"gt": ">", "lt": "<"}[evaluator["type"]]
    no_data = {"OK": 0, "Alerting": 1}[rule["noDataState"]]
    tests.append({
        "name": f"{uid}: {name}", "interval": "1m",
        "input_series": [{"series": metric, "values": value if isinstance(value, str) else f"{value}+0x10"}
                         for metric, value in metrics.items()],
        "promql_expr_test": [{
            "expr": f"max(({query}) {operator} bool {evaluator['params'][0]}) or vector({no_data})",
            "eval_time": "10m", "exp_samples": [{"labels": "{}", "value": expected}],
        }],
    })


readiness = {
    "sops-runtime-secrets-missing": {
        'sops_runtime_secrets_ready{instance="discovery"}': 1,
        'sops_runtime_secrets_ready{instance="kepler"}': 1,
    },
    "vault-agent-renders-stale": {
        'vault_agent_required_renders_ready{instance="discovery"}': 1,
    },
    "litellm-semantic-readiness-failed": {
        'litellm_semantic_ready{instance="discovery"}': 1,
    },
    "restic-kepler-offsite-transport-failed": {
        'restic_kepler_offsite_transport_ready{instance="kepler"}': 1,
    },
}
for uid, metrics in readiness.items():
    alert_case(uid, "healthy", metrics, 0)
    alert_case(uid, "all metrics missing", {}, 1)
    for metric in metrics:
        alert_case(uid, f"missing {metric}", {key: value for key, value in metrics.items() if key != metric}, 1)
        alert_case(uid, f"failed {metric}", {**metrics, metric: 0}, 1)
    if uid == "vault-agent-renders-stale":
        alert_case(uid, "healthy unchanged old file", {**metrics, 'vault_agent_render_last_success_seconds{instance="discovery"}': -1000}, 0)

# Authenticated request success and producer liveness are separate evidence.
probe_success = 'openbao_authenticated_probe_success{instance="discovery"}'
probe_timestamp = 'openbao_authenticated_probe_timestamp_seconds{instance="discovery"}'
for name, success, timestamp, expected in [
    ("healthy", 1, 600, 0),
    ("authentication or audit failure", 0, 600, 1),
    ("missing result", None, 600, 1),
    ("missing timestamp", 1, None, 1),
    ("all metrics missing", None, None, 1),
    ("ten-minute boundary", 1, 0, 0),
    ("stalled successful probe", 1, -1, 1),
]:
    alert_case("openbao-authenticated-probe-failed", name,
               {metric: value for metric, value in [(probe_success, success), (probe_timestamp, timestamp)]
                if value is not None}, expected)
assert all_rules["openbao-authenticated-probe-failed"]["for"] == "5m"
assert all_rules["openbao-authenticated-probe-failed"]["labels"]["instance"] == "discovery"

for uid, metric in [
    ("k8s-node-not-ready", 'kube_node_status_condition{cluster="homelab",node="cp-1",condition="Ready",status="true"}'),
    ("security-control-systemd-inactive", 'node_systemd_unit_state{instance="discovery",name="auditd.service",state="active"}'),
]:
    alert_case(uid, "active", {metric: 1}, 0)
    alert_case(uid, "inactive", {metric: 0}, 1)
    alert_case(uid, "missing metric", {}, 0)

for uid, numerator, denominator, labels, boundary in [
    ("host-memory-critical", "node_memory_MemAvailable_bytes", "node_memory_MemTotal_bytes", '{instance="kepler"}', 0.05),
    ("host-inode-low", "node_filesystem_files_free", "node_filesystem_files", '{instance="kepler",fstype="ext4",mountpoint="/"}', 0.05),
    ("voyager-disk-low", "node_filesystem_avail_bytes", "node_filesystem_size_bytes", '{instance="voyager",job="node-voyager",fstype="ext4",mountpoint="/"}', 0.15),
]:
    for name, available, expected in [("zero free", 0, 1), ("low free", 1, 1), ("boundary", boundary * 100, 0), ("healthy", 50, 0)]:
        alert_case(uid, name, {numerator + labels: available, denominator + labels: 100}, expected)
    alert_case(uid, "missing numerator", {denominator + labels: 100}, 0)
    alert_case(uid, "missing denominator", {numerator + labels: 0}, 0)

podman = {
    'up{instance="kepler",job="integrations/unix"}': 1,
    'podman_container_state{host="kepler",name="redis"}': 2,
}
for state in [0, 1, 2, 3]:
    alert_case("container-critical-down", f"Podman state {state}", {
        **podman, 'podman_container_state{host="kepler",name="postgres"}': state,
    }, int(state != 2))
alert_case("container-critical-down", "missing postgres", podman, 1)
alert_case("container-critical-down", "host scrape down", {
    'up{instance="kepler",job="integrations/unix"}': 0,
}, 0)

# Keep each recovery tier visible, including a missing producer beside healthy ones.
kepler_rule = all_rules["restic-kepler-backups-stale"]
kepler_expr = next(item["model"]["expr"] for item in kepler_rule["data"] if item["refId"] == "A")
for name, timestamps, expected in [
    ("healthy", [600, 600, 600], [0, 0, 0]),
    ("30h boundary", [-107400, -107400, -107400], [0, 0, 0]),
    ("stale offsite", [600, 600, -108000], [0, 0, 1]),
    ("missing postgres", [None, 600, 600], [1, 0, 0]),
    ("missing configs", [600, None, 600], [0, 1, 0]),
    ("missing offsite", [600, 600, None], [0, 0, 1]),
    ("all missing", [None, None, None], [1, 1, 1]),
]:
    metrics = {f'restic_kepler_{tier}_last_success_seconds{{instance="kepler"}}': value
               for tier, value in zip(["postgres", "configs", "offsite"], timestamps) if value is not None}
    alert_case("restic-kepler-backups-stale", name, metrics, max(expected))
    tests.append({
        "name": f"Kepler tier identity: {name}", "interval": "1m",
        "input_series": [{"series": metric, "values": f"{value}+0x10"} for metric, value in metrics.items()],
        "promql_expr_test": [{
            "expr": kepler_expr, "eval_time": "10m",
            "exp_samples": [{"labels": f'{{instance="kepler",backup="{tier}"}}', "value": value}
                            for tier, value in zip(["postgres", "configs", "offsite"], expected)],
        }],
    })

alloy_ready = {
    f'kube_pod_container_status_ready{{cluster="homelab",namespace="monitoring",pod="{pod}",container="alloy"}}': ready
    for pod, ready in [("alloy-logs-cp1", 1), ("alloy-metrics-abc", 0)]
}
alert_case("k8s-alloy-not-ready", "one healthy collector cannot hide an unready collector", alloy_ready, 1)
alloy_expr = next(item["model"]["expr"] for item in all_rules["k8s-alloy-not-ready"]["data"] if item["refId"] == "A")
tests[-1]["promql_expr_test"].append({
    "expr": alloy_expr, "eval_time": "10m",
    "exp_samples": [{"labels": f'{{cluster="homelab",namespace="monitoring",pod="{pod}",container="alloy"}}', "value": value}
                    for pod, value in [("alloy-logs-cp1", 0), ("alloy-metrics-abc", 1)]],
})
alert_case("k8s-alloy-not-ready", "all collectors healthy", dict.fromkeys(alloy_ready, 1), 0)
alert_case("k8s-alloy-not-ready", "all readiness telemetry missing", {}, 1)
# No expected pod roster exists: a missing individual series cannot imply failure.
alert_case("k8s-alloy-not-ready", "partial telemetry cannot identify vanished collector", {next(iter(alloy_ready)): 1}, 0)

oom_labels = 'cluster="homelab",namespace="monitoring",pod="alloy-metrics-abc",container="alloy",uid="current"'
oom_metric = f'kube_pod_container_status_last_terminated_reason{{{oom_labels},reason="OOMKilled"}}'
restart_metric = f'kube_pod_container_status_restarts_total{{{oom_labels}}}'
for name, reason, restarts, expected in [
    ("old OOM with no recent restart", 1, 3, 0),
    ("new OOM restart", 1, "3+0x8 4+0x1", 1),
    ("new restart after old OOM has another reason", "1+0x8 0+0x1", "3+0x8 4+0x1", 0),
    ("counter reset is not a new OOM", 1, "3+0x8 0+0x1", 0),
    ("restart telemetry missing", 1, None, 0),
    ("reason telemetry missing", None, "3+0x8 4+0x1", 0),
]:
    alert_case("k8s-alloy-oom", name, {metric: value for metric, value in [(oom_metric, reason), (restart_metric, restarts)] if value is not None}, expected)
alert_case("k8s-alloy-oom", "different pod UID cannot supply restart evidence", {
    oom_metric: 1, restart_metric.replace('uid="current"', 'uid="previous"'): "3+0x8 4+0x1",
}, 0)

job_labels = 'cluster="homelab",namespace="cognee",job_name="cognee-backup-123"'
for name, failed_attempts, terminal_failed, succeeded, expected in [
    ("retry then succeeded", 1, 0, 1, 0),
    ("retry still running", 1, 0, 0, 0),
    ("terminal failure", 2, 1, 0, 1),
]:
    alert_case("k8s-job-failed", name, {
        f'kube_job_status_failed{{{job_labels}}}': failed_attempts,
        f'kube_job_failed{{{job_labels},condition="true"}}': terminal_failed,
        f'kube_job_status_succeeded{{{job_labels}}}': succeeded,
    }, expected)
alert_case("k8s-job-failed", "terminal condition telemetry missing", {
    f'kube_job_status_failed{{{job_labels}}}': 1,
}, 0)

host_metrics = {f'up{{instance="{host}",job="integrations/unix"}}': 1
                for host in ["discovery", "kepler", "orion", "apollo"]}
alert_case("host-metrics-absent", "all always-on hosts present", host_metrics, 0)
alert_case("host-metrics-absent", "Discovery vanished beside healthy hosts", {
    metric: value for metric, value in host_metrics.items() if 'instance="discovery"' not in metric
}, 1)
host_expr = next(item["model"]["expr"] for item in all_rules["host-metrics-absent"]["data"] if item["refId"] == "A")
tests[-1]["promql_expr_test"].append({
    "expr": host_expr, "eval_time": "10m",
    "exp_samples": [{"labels": '{instance="discovery",job="integrations/unix"}', "value": 1}],
})
alert_case("host-metrics-absent", "failed scrape remains scrape-down responsibility", {
    **host_metrics, 'up{instance="discovery",job="integrations/unix"}': 0,
}, 0)
alert_case("host-metrics-absent", "short Discovery gap is tolerated", {
    **host_metrics, 'up{instance="discovery",job="integrations/unix"}': "1+0x3 stale _x6",
}, 0)
alert_case("host-metrics-absent", "Discovery silent for more than 20m", {
    **dict.fromkeys(host_metrics, "1+0x30"),
    'up{instance="discovery",job="integrations/unix"}': "1+0x3 stale _x26",
}, 1)
tests[-1]["promql_expr_test"][0]["eval_time"] = "30m"

with tempfile.TemporaryDirectory(prefix="monitoring-promql-") as directory:
    path = Path(directory) / "tests.yaml"
    path.write_text(yaml.safe_dump({"evaluation_interval": "1m", "tests": tests}))
    subprocess.run(["promtool", "test", "rules", str(path)], check=True)
