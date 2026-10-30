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

import acquire_image
import yaml
from check_payload import IMAGE, SINK, check_payload, check_tls_payload
from tls_fixture import CASES, PHASES, SCRAPES, target_port, tls_config

SOURCE_SHA256 = "995ee316a76f4c34b01c659aa356cf8029fd159579a529f219416274c30dddcc"


def check_artifacts(chart, pins):
    values = yaml.safe_load((chart / "values.yaml").read_text())
    wrapper = yaml.safe_load((chart / "Chart.yaml").read_text())
    with tarfile.open(chart / "charts/alloy-1.14.3.tgz") as archive:
        metadata = yaml.safe_load(archive.extractfile("alloy/Chart.yaml").read())
    expected = {
        "chart_version": wrapper["dependencies"][0]["version"],
        "image_version": metadata["appVersion"],
        "image_index_digest": values["alloy"]["image"]["digest"],
        "image_manifest_digest": IMAGE.split("@", 1)[1],
        "platform": "linux/amd64",
    }
    if any(pins.get(key) != value for key, value in expected.items()):
        raise AssertionError("chart, configured image and artifact metadata disagree")
    if metadata["version"] != pins["chart_version"] or not re.fullmatch(
        r"v\d+\.\d+\.\d+", pins["image_version"]
    ):
        raise AssertionError("unexpected chart or image version")
    acquisition = {
        "image_index_digest": acquire_image.INDEX,
        "image_manifest_digest": acquire_image.MANIFEST,
        "image_config_digest": acquire_image.CONFIG,
        "image_archive_sha256": acquire_image.ARCHIVE_SHA256,
    }
    if any(pins.get(key) != value for key, value in acquisition.items()):
        raise AssertionError("artifact acquisition pins disagree")


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


def tls_documents(source_config, run_id, phase):
    name = "alloy-tls-" + phase
    ports = [
        {
            "name": f"tls-{target_port(scrape, case)}",
            "port": target_port(scrape, case),
            "targetPort": target_port(scrape, case),
        }
        for scrape in SCRAPES
        for case in CASES
    ]
    return [
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": name, "namespace": "monitoring"},
            "data": {"config.alloy": tls_config(source_config, run_id, phase)},
        },
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": name, "namespace": "monitoring"},
            "spec": {
                "automountServiceAccountToken": False,
                "restartPolicy": "Never",
                "containers": [
                    {
                        "name": "alloy",
                        "image": IMAGE,
                        "imagePullPolicy": "Never",
                        "command": ["/bin/alloy"],
                        "args": [
                            "run",
                            "/etc/alloy/config.alloy",
                            "--server.http.listen-addr=0.0.0.0:12345",
                            "--storage.path=/tmp/alloy-tls",
                        ],
                        "resources": {
                            "requests": {"cpu": "25m", "memory": "128Mi"},
                            "limits": {"memory": "512Mi"},
                        },
                        "volumeMounts": [
                            {
                                "name": "config",
                                "mountPath": "/etc/alloy",
                                "readOnly": True,
                            },
                            {
                                "name": "identity",
                                "mountPath": "/var/run/secrets/kubernetes.io/serviceaccount",
                                "readOnly": True,
                            },
                        ],
                    }
                ],
                "volumes": [
                    {"name": "config", "configMap": {"name": name}},
                    {
                        "name": "identity",
                        "secret": {
                            "secretName": "alloy-tls-identity",
                            "defaultMode": 384,
                        },
                    },
                ],
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "alloy-tls-witness", "namespace": "monitoring"},
            "spec": {"ports": ports},
        },
        {
            "apiVersion": "v1",
            "kind": "Endpoints",
            "metadata": {"name": "alloy-tls-witness", "namespace": "monitoring"},
            "subsets": [
                {
                    "addresses": [{"ip": "192.168.1.2"}],
                    "ports": [{"name": p["name"], "port": p["port"]} for p in ports],
                }
            ],
        },
    ]


def render(chart, output, run_id):
    pins = json.loads(pathlib.Path(__file__).with_name("artifacts.json").read_text())
    check_artifacts(chart, pins)
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
    for phase in PHASES:
        documents = tls_documents(source_config, run_id, phase)
        check_tls_payload(documents, source_config, run_id, phase)
        (output / ("tls-" + phase + ".yaml")).write_text(
            yaml.safe_dump_all(documents, sort_keys=False)
        )
    receipt = {
        "run_id": run_id,
        "image": IMAGE,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "files": {
            name: hashlib.sha256((output / name).read_bytes()).hexdigest()
            for name in [
                "current.yaml",
                "legacy-rbac.yaml",
                *["tls-" + phase + ".yaml" for phase in PHASES],
            ]
        },
    }
    (output / "payload.json").write_text(json.dumps(receipt, sort_keys=True) + "\n")


if __name__ == "__main__":
    chart, output, run_id = sys.argv[1:]
    render(pathlib.Path(chart), pathlib.Path(output), run_id)
