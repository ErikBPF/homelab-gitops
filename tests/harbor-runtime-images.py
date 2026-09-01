#!/usr/bin/env python3
import pathlib
import subprocess

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
HARBOR = "harbor.homelab.pastelariadev.com/"
COVERED = ("docker.io/", "ghcr.io/", "quay.io/", "lscr.io/")
COMPONENTS = (
    "argocd",
    "apps/cognee",
    "apps/home-services/changedetection",
    "apps/home-services/immich",
    "apps/home-services/karakeep",
    "apps/homelab/airflow",
    "apps/homelab/demo",
    "apps/homelab/trace-demo",
    "platform/alloy-metrics",
    "platform/authentik",
    "platform/cert-manager",
    "platform/clickhouse-operator",
    "platform/healthchecks",
    "platform/loki",
    "platform/monitoring",
    "platform/scrutiny",
    "platform/uptime-kuma",
    "platform/wazuh",
)


def images(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "image" and isinstance(child, str):
                yield child
            yield from images(child)
    elif isinstance(value, list):
        for child in value:
            yield from images(child)


def documents(component):
    path = ROOT / component
    if (path / "Chart.yaml").exists():
        output = subprocess.check_output(["helm", "template", path.name, path], text=True)
    elif (path / "kustomization.yaml").exists():
        output = subprocess.check_output(["kubectl", "kustomize", path], text=True)
    else:
        output = "\n---\n".join(file.read_text() for file in sorted(path.glob("*.yaml")))
    yield from yaml.safe_load_all(output)


def external_covered(image):
    if image.startswith(HARBOR):
        return False
    if image.startswith(COVERED):
        return True
    name = image.split("@", 1)[0]
    if "/" not in name:
        return True
    first = name.split("/", 1)[0]
    return "." not in first and ":" not in first and first != "localhost"


violations = []
for component in COMPONENTS:
    for document in documents(component):
        if document:
            violations.extend(
                f"{component}: {image}"
                for image in images(document)
                if external_covered(image)
            )

assert not violations, "runtime images bypass Harbor:\n" + "\n".join(sorted(set(violations)))
