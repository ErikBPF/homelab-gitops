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
PY
