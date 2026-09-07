#!/usr/bin/env python3
"""Evaluate the deployed Apollo expression: promtool and PyYAML required.

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
with tempfile.TemporaryDirectory(prefix="monitoring-promql-") as directory:
    path = Path(directory) / "tests.yaml"
    path.write_text(yaml.safe_dump({"evaluation_interval": "1m", "tests": tests}))
    subprocess.run(["promtool", "test", "rules", str(path)], check=True)
