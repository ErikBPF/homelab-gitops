"""Prepare the reviewed, test-only overlay; never edit production values."""

import copy
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tarfile
import tempfile

import yaml
from check_payload import IMAGE, SINK, check_payload

SOURCE_SHA256 = "9d87ff08f25869c0765a5e2e838df8ce550a3cd96d235be9cf70fc2642ff25d0"


def prepare(source, run_id):
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise AssertionError("source changed: review fixture transformations")
    if not re.fullmatch("[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", run_id):
        raise AssertionError("invalid run ID")
    values = yaml.safe_load(source)
    alloy = values["alloy"]
    config = alloy["alloy"]["configMap"]["content"]
    for component, name in [
        ("prometheus.scrape", "ksm"),
        ("prometheus.scrape", "etcd"),
        ("prometheus.relabel", "etcd_keep"),
    ]:
        config, count = re.subn(
            f'^{re.escape(component)} "{name}" \\{{\\n.*?^\\}}\\n',
            "",
            config,
            flags=re.MULTILINE | re.DOTALL,
        )
        if count != 1:
            raise AssertionError(f"missing expected component: {component} {name}")
    old_sink = "http://monitoring-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090/api/v1/write"
    if config.count(old_sink) != 1:
        raise AssertionError("fixture boundary failed")
    config = config.replace(old_sink, SINK)
    old_labels = 'external_labels = { "cluster" = "homelab" }'
    if config.count(old_labels) != 1:
        raise AssertionError("fixture boundary failed")
    config = config.replace(
        old_labels, f'external_labels = {{ "cluster" = "{run_id}" }}'
    )
    alloy["alloy"]["configMap"]["content"] = config
    alloy["image"] = {
        "registry": "docker.io",
        "repository": "grafana/alloy",
        "digest": IMAGE.split("@", 1)[1],
        "pullPolicy": "Never",
    }
    alloy["configReloader"] = {"enabled": False, "image": {"registry": "quay.io"}}
    return values


def render(chart, output, run_id):
    source = (chart / "values.yaml").read_bytes()
    values = prepare(source, run_id)
    with tarfile.open(chart / "charts/alloy-1.14.3.tgz") as archive:
        upstream = yaml.safe_load(archive.extractfile("alloy/values.yaml").read())
    legacy_values = copy.deepcopy(values)
    legacy_values["alloy"]["rbac"] = upstream["rbac"]
    rendered = []
    with tempfile.TemporaryDirectory() as directory:
        for name, overlay in [("current", values), ("legacy", legacy_values)]:
            path = pathlib.Path(directory) / (name + ".yaml")
            path.write_text(yaml.safe_dump(overlay))
            raw = subprocess.check_output(
                [
                    "helm",
                    "template",
                    "alloy-metrics",
                    str(chart),
                    "--namespace",
                    "monitoring",
                    "--values",
                    str(path),
                ],
                text=True,
            )
            rendered.append([item for item in yaml.safe_load_all(raw) if item])
    current, legacy = rendered
    source_config = yaml.safe_load(source)["alloy"]["alloy"]["configMap"]["content"]
    check_payload(current, source_config, run_id)

    def identity(item):
        return (
            item["kind"],
            item["metadata"].get("namespace", ""),
            item["metadata"]["name"],
        )

    current_ids = {identity(item) for item in current}
    obsolete = [
        item
        for item in legacy
        if item["kind"] in ["ClusterRole", "ClusterRoleBinding"]
        and identity(item) not in current_ids
    ]
    if len(obsolete) != 2 or {item["kind"] for item in obsolete} != {
        "ClusterRole",
        "ClusterRoleBinding",
    }:
        raise AssertionError("expected exactly the old chart role and binding")
    output.mkdir(parents=True, exist_ok=True)
    (output / "current.yaml").write_text(yaml.safe_dump_all(current, sort_keys=False))
    (output / "legacy-rbac.yaml").write_text(
        yaml.safe_dump_all(obsolete, sort_keys=False)
    )
    receipt = {
        "run_id": run_id,
        "image": IMAGE,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "files": {
            name: hashlib.sha256((output / name).read_bytes()).hexdigest()
            for name in ["current.yaml", "legacy-rbac.yaml"]
        },
    }
    (output / "payload.json").write_text(json.dumps(receipt, sort_keys=True) + "\n")


if __name__ == "__main__":
    chart, output, run_id = sys.argv[1:]
    render(pathlib.Path(chart), pathlib.Path(output), run_id)
