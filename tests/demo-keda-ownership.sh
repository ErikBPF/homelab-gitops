#!/usr/bin/env bash
set -euo pipefail

if grep -q '^[[:space:]]*replicas:' apps/homelab/demo/deployment.yaml; then
  echo "KEDA-managed demo Deployment must not declare spec.replicas" >&2
  exit 1
fi

python3 - <<'PY'
import pathlib
import re
import subprocess

import yaml

path = pathlib.Path("platform/keda")
dependency = yaml.safe_load((path / "Chart.yaml").read_text())["dependencies"][0]
chart = path / "charts" / f"keda-{dependency['version']}.tgz"
app_version = yaml.safe_load(subprocess.check_output(["helm", "show", "chart", chart]))["appVersion"]
values = yaml.safe_load((path / "values.yaml").read_text())["keda"]
for component in ("keda", "metricsApiServer", "webhooks"):
    tag = values["image"][component]["tag"]
    assert re.fullmatch(re.escape(app_version) + r"@sha256:[0-9a-f]{64}", tag), (
        f"KEDA {component} image must match chart appVersion {app_version} with an immutable digest; got {tag}"
    )

# The incident workaround changes only metrics placement, never its resource budget.
def deployments(extra=()):
    rendered = subprocess.check_output([
        "helm", "template", "keda", str(path), "--namespace", "keda", *extra
    ], text=True)
    return {doc["metadata"]["name"]: doc for doc in yaml.safe_load_all(rendered)
            if doc and doc["kind"] == "Deployment"}

actual = deployments()
metrics = actual["keda-operator-metrics-apiserver"]
spec = metrics["spec"]["template"]["spec"]
assert spec["nodeSelector"].get("kubernetes.io/hostname") == "w-2", (
    "metrics apiserver must avoid the observed w-1 exit139 path on proven w-2"
)
assert spec["containers"][0]["resources"]["requests"] == {"cpu": "10m", "memory": "64Mi"}
baseline = deployments(("--set-json", "keda.metricsServer.nodeSelector=null"))
# Removing the sole temporary selector must reproduce the pre-workaround render.
del spec["nodeSelector"]["kubernetes.io/hostname"]
assert actual == baseline, "workaround must not change other components or resources"
PY
