#!/usr/bin/env python3
"""Evaluate provisioned alert expressions: promtool and PyYAML required.

Run: nix shell nixpkgs#prometheus.cli --command python3 tests/monitoring-promql.py
"""
from pathlib import Path
import subprocess
import tempfile

import yaml

root = Path(__file__).resolve().parents[1]
tests = []

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


# The only enrolled endpoint must alert when inactive or its telemetry disappears.
wazuh_uid = "wazuh-orion-agent-unavailable"
assert wazuh_uid in all_rules, "Wazuh agent loss has no provisioned alert"
wazuh_metric = 'node_systemd_unit_state{instance="orion",name="podman-wazuh-agent.service",state="active"}'
for name, metrics, expected in [
    ("active", {wazuh_metric: 1}, 0),
    ("inactive", {wazuh_metric: 0}, 1),
    ("missing telemetry", {}, 1),
    ("another host cannot mask missing Orion", {wazuh_metric.replace('instance="orion"', 'instance="kepler"'): 1}, 1),
    ("another service cannot mask missing agent", {wazuh_metric.replace('podman-wazuh-agent.service', 'sshd.service'): 1}, 1),
]:
    alert_case(wazuh_uid, name, metrics, expected)
assert all_rules[wazuh_uid]["for"] == "5m"
assert all_rules[wazuh_uid]["labels"]["instance"] == "orion"
assert all_rules[wazuh_uid]["labels"]["name"] == "podman-wazuh-agent.service"

readiness = {
    "sops-runtime-secrets-missing": {
        'sops_runtime_secrets_ready{instance="discovery"}': 1,
        'sops_runtime_secrets_ready{instance="kepler"}': 1,
    },
    "vault-agent-renders-stale": {
        'vault_agent_required_renders_ready{instance="discovery"}': 1,
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

for uid, threshold in [("authentik-offsite-aging", 18 * 3600),
                       ("authentik-offsite-rpo-breached", 24 * 3600)]:
    assert uid in all_rules, f"missing Authentik source-age alert: {uid}"
    metric = 'restic_authentik_offsite_source_timestamp_seconds{instance="kepler"}'
    for name, value, expected in [("fresh", 500, 0),
                                  ("at threshold", 600 - threshold, 1),
                                  ("missing", None, 1),
                                  ("future", 700, 1)]:
        alert_case(uid, name, {} if value is None else {metric: value}, expected)

# Integrity expectations are injected independently of producer telemetry.
integrity_uids = ["host-integrity-finding", "host-integrity-incomplete",
                  "host-integrity-stale", "host-integrity-signature-age",
                  "host-integrity-missing-check"]
for uid in integrity_uids:
    assert uid in all_rules, f"missing integrity rule: {uid}"
    assert all_rules[uid]["noDataState"] == "OK"
    assert all_rules[uid]["execErrState"] == "KeepLast"


def integrity_case(name, overrides=None, firing=(), check="store", enrolled=True, host=True, instance="orion"):
    # Ten days permits real positive timestamps for eight-day boundary tests.
    now = 864000
    values = {"state": 2, "last_attempt_seconds": now - 60,
              "last_complete_seconds": now - 60, "last_publish_seconds": now - 60,
              "findings": 0, "skipped": 0, "errors": 0}
    if check == "signatures":
        values["signature_database_seconds"] = now - 60
    values.update(overrides or {})
    identity = f'instance="{instance}",check="{check}"'
    metrics = {f'host_integrity_{key}{{{identity},job="collector"}}': value
               for key, value in values.items() if value is not None}
    if enrolled:
        metrics[f'host_integrity_expected{{{identity}}}'] = 1
    if host:
        metrics[f'up{{instance="{instance}",job="integrations/unix"}}'] = host if isinstance(host, str) else 1
    # A healthy peer proves absence is evaluated per identity.
    metrics['host_integrity_expected{instance="apollo",check="store"}'] = 1
    metrics['up{instance="apollo",job="integrations/unix"}'] = 1
    for key, value in {**values, "state": 2, "findings": 0, "skipped": 0,
                       "errors": 0, "last_complete_seconds": now - 60,
                       "last_publish_seconds": now - 60}.items():
        metrics[f'host_integrity_{key}{{instance="apollo",check="store"}}'] = now - 60 if value is None else value
    expressions = []
    for uid in integrity_uids:
        query = next(item["model"]["expr"] for item in all_rules[uid]["data"] if item["refId"] == "A")
        expressions.append({"expr": query, "eval_time": "240h", "exp_samples":
                            [{"labels": "{" + identity + "}", "value": 1}] if uid.removeprefix("host-integrity-") in firing else []})
    tests.append({"name": "integrity: " + name, "interval": "1h",
                  "input_series": [{"series": metric, "values": value if isinstance(value, str) else f"{value}+0x240"}
                                   for metric, value in metrics.items()],
                  "promql_expr_test": expressions})


integrity_case("healthy")
integrity_case("fresh finding", {"findings": 2}, ["finding"])
for state in [3, 4]:
    integrity_case(f"finding survives state {state}", {"state": state, "findings": 2,
                   "last_complete_seconds": 1}, ["finding", "incomplete"])
integrity_case("never run", {"state": 0, "last_complete_seconds": 0}, ["missing-check"])
integrity_case("missing one check", {key: None for key in ["state", "last_attempt_seconds",
               "last_complete_seconds", "last_publish_seconds", "findings", "skipped", "errors"]}, ["missing-check"])
integrity_case("missing required gauge", {"last_complete_seconds": None}, ["missing-check"])
integrity_case("whole host absent", {"state": None}, host=False)
integrity_case("unsupported or unenrolled", {"state": 4, "findings": 2}, enrolled=False)
for check, age in [("store", 48 * 3600), ("av", 8 * 86400)]:
    integrity_case(f"{check} exact boundary", {"last_complete_seconds": 864000-age}, check=check)
    integrity_case(f"{check} expired", {"last_complete_seconds": 864000-age-1}, ["stale"], check=check)
integrity_case("fresh publish cannot renew coverage", {"last_complete_seconds": 1}, ["stale"])
integrity_case("running first scan expires", {"state": 1, "last_complete_seconds": 0}, ["stale"])
integrity_case("signature exact boundary", {"signature_database_seconds": 864000-172800}, check="signatures")
integrity_case("updater success cannot renew database", {"signature_database_seconds": 1}, ["signature-age"], check="signatures")
integrity_case("missing database evidence", {"signature_database_seconds": None}, ["missing-check"], check="signatures")
integrity_case("unknown database", {"signature_database_seconds": 0}, ["signature-age"], check="signatures")
integrity_case("recovered coverage retains finding", {"findings": 1}, ["finding"])
integrity_case("reviewed resolution", {"findings": 0})

# Endeavour is intermittent: sleeping is not recovery or an always-on outage.
for check in ["signatures", "store", "av"]:
    integrity_case(f"Endeavour sleeping {check}", {"state": 4, "last_complete_seconds": 1},
                   check=check, instance="endeavour", host="1+0x238 stale _")
    integrity_case(f"Endeavour awake overdue {check}", {"last_complete_seconds": 1},
                   ["stale"], check=check, instance="endeavour", host="0+0x239 1")
integrity_case("Endeavour scrape failed is existing host rule responsibility",
               {"state": 4}, instance="endeavour", host="0+0x240")
integrity_case("Endeavour sleep cannot clear retained finding while samples exist",
               {"state": 4, "findings": 1}, ["finding"], instance="endeavour", host=False)
# The healthy-scrape gate is load-bearing: without a live up series the
# intermittency rules must stay silent even when coverage is overdue.
integrity_case("Endeavour overdue without a scrape", {"last_complete_seconds": 1},
               instance="endeavour", host=False)
integrity_case("Endeavour incomplete without a scrape", {"state": 3},
               instance="endeavour", host=False)
assert 'endeavour' not in host_expr, "intermittent pilot must not enter always-on host absence inventory"

# Observe loss and recovery across samples, preserving per-check identities.
for name, transition, firing in [
    ("series disappears while host remains", "2+0x238 stale _", ["missing-check"]),
    ("failed rerun", "2+0x239 4", ["incomplete"]),
    ("coverage recovers", "4+0x239 2", []),
]:
    integrity_case(name, firing=firing)
    for series in tests[-1]["input_series"]:
        if series["series"].startswith('host_integrity_state{instance="orion"'):
            series["values"] = transition

# S09 v1: a single independently expected daily job on Discovery.
drift_uids = ["homelab-iac-drift-execution-health", "homelab-iac-drift-delivery-health",
              "homelab-iac-drift-stale", "homelab-iac-drift-missing"]
for uid in drift_uids:
    assert uid in all_rules, f"missing IaC lifecycle health rule: {uid}"
    assert all_rules[uid]["noDataState"] == "OK"
    assert all_rules[uid]["execErrState"] == "KeepLast"
    assert all_rules[uid]["for"] == "5m"


def drift_case(name, overrides=None, firing=(), host=1):
    now = 864000
    values = {"last_attempt_seconds": now - 60, "last_complete_seconds": now - 40,
              "last_publish_seconds": now - 40, "duration_seconds": 20,
              "outcome": 2, "notification": 0, "lock_contention": 0}
    values.update(overrides or {})
    metrics = {f'homelab_iac_drift_{key}{{instance="discovery",job="collector"}}': value
               for key, value in values.items() if value is not None}
    if host is not None:
        metrics['up{instance="discovery",job="integrations/unix"}'] = host
    # Healthy foreign telemetry cannot fill Discovery's missing job.
    metrics['up{instance="kepler",job="integrations/unix"}'] = 1
    for key in values:
        metrics[f'homelab_iac_drift_{key}{{instance="kepler"}}'] = now - 40 if key.endswith('_seconds') else 0
    expressions = []
    for uid in drift_uids:
        query = next(item["model"]["expr"] for item in all_rules[uid]["data"] if item["refId"] == "A")
        expressions.append({"expr": query, "eval_time": "240h", "exp_samples":
                            [{"labels": '{instance="discovery"}', "value": 1}] if uid in firing else []})
    tests.append({"name": "IaC health: " + name, "interval": "1h",
                  "input_series": [{"series": metric, "values": value if isinstance(value, str) else f"{value}+0x240"}
                                   for metric, value in metrics.items()],
                  "promql_expr_test": expressions})


execution, delivery, stale, missing = drift_uids
drift_case("clean completion")
drift_case("completed drift belongs to existing finding notifier", {"outcome": 3, "notification": 1})
for outcome in [4, 5, 6]:
    drift_case(f"finished failure {outcome} takes precedence over old completion",
               {"outcome": outcome, "last_complete_seconds": 1, "notification": 4}, [execution])
for notification in [2, 3, 4]:
    drift_case(f"completed drift delivery health {notification}",
               {"outcome": 3, "notification": notification}, [delivery])
drift_case("running at whole-service grace boundary", {"outcome": 1, "last_attempt_seconds": 864000-3780})
drift_case("running beyond whole-service grace boundary", {"outcome": 1, "last_attempt_seconds": 864000-3781}, [execution])
drift_case("26h completion boundary", {"last_complete_seconds": 864000-93600})
drift_case("26h completion expired", {"last_complete_seconds": 864000-93601}, [stale])
drift_case("fresh publication cannot renew old completion", {"last_complete_seconds": 1}, [stale])
drift_case("never run", {"outcome": 0, "last_complete_seconds": 0}, [missing])
for metric in ["last_attempt_seconds", "last_complete_seconds", "last_publish_seconds",
               "duration_seconds", "outcome", "notification", "lock_contention"]:
    drift_case(f"missing required {metric}", {metric: None}, [missing])
drift_case("entire job absent beside healthy peer", {key: None for key in [
    "last_attempt_seconds", "last_complete_seconds", "last_publish_seconds",
    "duration_seconds", "outcome", "notification", "lock_contention"]}, [missing])
drift_case("whole host missing belongs to host-loss rule", {"outcome": None}, host=None)
drift_case("failed host scrape belongs to scrape-down rule", {"outcome": 4}, host=0)
drift_case("timeout then clean recovery", {"outcome": "5+0x239 2"})
drift_case("job disappears while host remains", {"outcome": "2+0x238 stale _"}, [missing])
# Keep the generic fallback: old successful lifecycle gauges cannot conceal a new unit failure.
alert_case("host-systemd-unit-failed", "IaC reporter failure with old healthy lifecycle", {
    'node_systemd_unit_state{instance="discovery",name="homelab-iac-drift.service",state="failed"}': 1,
    'homelab_iac_drift_outcome{instance="discovery"}': 2,
    'homelab_iac_drift_last_complete_seconds{instance="discovery"}': 500,
}, 1)

with tempfile.TemporaryDirectory(prefix="monitoring-promql-") as directory:
    path = Path(directory) / "tests.yaml"
    path.write_text(yaml.safe_dump({"evaluation_interval": "1m", "tests": tests}))
    subprocess.run(["promtool", "test", "rules", str(path)], check=True)
