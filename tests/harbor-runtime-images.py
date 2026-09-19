#!/usr/bin/env python3
import json
import pathlib
import subprocess

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
HARBOR = "harbor.homelab.pastelariadev.com/"
COVERED = (
    "docker.io/",
    "docker.langfuse.com/",
    "docker.risingwave.com/",
    "ghcr.io/",
    "index.docker.io/",
    "quay.io/",
    "registry-1.docker.io/",
    "lscr.io/",
    "registry.k8s.io/",
)
COMPONENTS = (
    "argocd",
    "apps/cognee",
    "apps/home-services/changedetection",
    "apps/home-services/immich",
    "apps/home-services/karakeep",
    "apps/homelab/airflow",
    "apps/homelab/demo",
    "apps/homelab/langfuse",
    "apps/homelab/trace-demo",
    "platform/alloy-metrics",
    "platform/authentik",
    "platform/cert-manager",
    "platform/clickhouse-operator",
    "platform/csi-driver-nfs",
    "platform/external-secrets",
    "platform/healthchecks",
    "platform/jaeger",
    "platform/keda",
    "platform/kube-state-metrics",
    "platform/loki",
    "platform/monitoring",
    "platform/otel-collector",
    "platform/spark-operator",
    "platform/scrutiny",
    "platform/traefik",
    "platform/spark-connect",
    "platform/trino",
    "platform/trino-gateway",
    "platform/uptime-kuma",
    "platform/wazuh",
    "platform/yunikorn",
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
    elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            yield from images(json.loads(value))
        except json.JSONDecodeError:
            pass


def documents(component):
    path = ROOT / component
    assert path.exists(), f"missing Harbor-controlled component: {component}"
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
                or (image.startswith(HARBOR) and "@sha256:" not in image)
            )

assert not violations, "runtime images bypass Harbor:\n" + "\n".join(sorted(set(violations)))

yunikorn = yaml.safe_load((ROOT / "platform/yunikorn/values.yaml").read_text())
assert "@sha256:" in yunikorn["yunikorn"]["yunikornDefaults"]["service.placeholderImage"]
