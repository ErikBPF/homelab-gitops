#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
repo=ssh://git@ssh.github.com:443/ErikBPF/homelab-gitops.git
apps=$root/clusters/kepler/apps

for service in airflow demo trace-demo; do
  test -d "$root/apps/homelab/$service"
  test ! -e "$root/apps/$service"
done

projects=$apps/projects.yaml
test -f "$projects"
python - "$projects" <<'PY'
import pathlib
import sys

import yaml

assert len(list(yaml.safe_load_all(pathlib.Path(sys.argv[1]).read_text()))) == 3
PY

for project in homelab home-services; do
  test "$(yq -r "select(.metadata.name == \"$project\") | .spec.sourceRepos[]" "$projects")" = "$repo"
done

# Only the declared recovery rollout dependencies extend platform source access.
python - "$root" <<'PYTEST'
from pathlib import Path
import sys
import yaml
root = Path(sys.argv[1])
projects = list(yaml.safe_load_all((root / 'clusters/kepler/apps/projects.yaml').read_text()))
platform = next(project for project in projects if project['metadata']['name'] == 'platform')
expected = {
    'ssh://git@ssh.github.com:443/ErikBPF/homelab-gitops.git',
    'https://charts.goauthentik.io',
    'https://prometheus-community.github.io/helm-charts',
}
actual = platform['spec']['sourceRepos']
assert set(actual) == expected and len(actual) == len(expected), actual
for name in ('authentik', 'monitoring'):
    chart = yaml.safe_load((root / 'platform' / name / 'Chart.yaml').read_text())
    assert all(dep['repository'] in expected for dep in chart['dependencies']), name
PYTEST

test "$(yq -r 'select(.metadata.name == "homelab") | .spec.destinations[].namespace' "$projects" | sort | paste -sd, -)" = "airflow,cognee,demo,langfuse,litellm,mcp-echo,openwebui,trace-demo"
test "$(yq -r 'select(.metadata.name == "home-services") | .spec.destinations[].namespace' "$projects" | sort | paste -sd, -)" = "changedetection,immich,karakeep"

if grep -R -l '^kind: Namespace$' "$root/apps/homelab" "$root/apps/home-services"; then
  echo 'lane applications must not own cluster-scoped namespaces' >&2
  exit 1
fi
test "$(yq -r 'select(.kind == "Namespace" and .metadata.name == "demo") | .metadata.labels."pod-security.kubernetes.io/enforce"' "$apps/namespaces.yaml")" = restricted
test -z "$(yq -r 'select(.kind == "Namespace" and .metadata.name == "external-secrets") | .metadata.name' "$apps/namespaces.yaml")"
test "$(yq -r '.spec.syncPolicy.syncOptions[]' "$apps/external-secrets.yaml" | grep -Fx 'CreateNamespace=true')" = 'CreateNamespace=true'

if yq -r '.. | select(tag == "!!str")' "$projects" | grep -Fxq '*'; then
  echo 'AppProjects must not grant wildcard authority' >&2
  exit 1
fi

for app in argocd alloy-metrics csi-driver-nfs external-secrets jaeger keda kube-state-metrics otel-collector spark-operator traefik yunikorn; do
  test "$(yq -r '.spec.project' "$apps/$app.yaml")" = platform
done

for app in airflow demo trace-demo; do
  test "$(yq -r '.spec.project' "$apps/$app.yaml")" = homelab
  test "$(yq -r '.spec.source.path' "$apps/$app.yaml")" = "apps/homelab/$app"
done

test "$(yq -r '.spec.destination.namespace' "$apps/trace-demo.yaml")" = trace-demo
test "$(yq -r '.metadata.namespace' "$root/apps/homelab/trace-demo/deployment.yaml")" = trace-demo

python - "$apps" <<'PY'
import pathlib
import sys

import yaml

root = pathlib.Path(sys.argv[1])
namespaces = {
    document["metadata"]["name"]
    for document in yaml.safe_load_all((root / "namespaces.yaml").read_text())
}
for path in root.glob("*.yaml"):
    for document in yaml.safe_load_all(path.read_text()):
        if not document or document.get("kind") != "Application":
            continue
        project = document["spec"]["project"]
        namespace = document["spec"]["destination"]["namespace"]
        if project in {"homelab", "home-services"}:
            options = document["spec"].get("syncPolicy", {}).get("syncOptions", [])
            assert "CreateNamespace=true" not in options, path
            assert namespace in namespaces, (path, namespace)
PY

grep -Eq '^test:' "$root/justfile"
grep -Fq 'scripts/validate.sh' "$root/justfile"
grep -Fq 'run: scripts/validate.sh' "$root/.github/workflows/validate.yml"
