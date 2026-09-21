#!/usr/bin/env python3
import pathlib
import re
import subprocess

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPONENTS = (
    "apps/home-services/changedetection",
    "apps/home-services/immich",
    "apps/home-services/karakeep",
    "apps/homelab/langfuse",
    "platform/cert-manager",
    "platform/clickhouse-operator",
    "platform/monitoring",
    "platform/spark-connect",
    "platform/trino",
    "platform/trino-gateway",
    "platform/loki",
    "platform/scrutiny",
    "platform/wazuh",
)


def rendered(component):
    path = ROOT / component
    if (path / "Chart.yaml").exists():
        return subprocess.check_output(["helm", "template", path.name, path], text=True)
    if (path / "kustomization.yaml").exists():
        return subprocess.check_output(["kubectl", "kustomize", path], text=True)
    return "\n---\n".join(file.read_text() for file in sorted(path.glob("*.yaml")))


def images(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "image" and isinstance(child, str):
                yield child
            yield from images(child)
    elif isinstance(value, list):
        for child in value:
            yield from images(child)


mutable = []
for component in COMPONENTS:
    for document in yaml.safe_load_all(rendered(component)):
        if document:
            mutable.extend(
                f"{component}: {image}"
                for image in images(document)
                if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", image)
            )

assert not mutable, "mutable first-wave images:\n" + "\n".join(sorted(set(mutable)))
