#!/usr/bin/env bash
set -euo pipefail

# First-party manifests: upstream 1.98.0/1.100.1 hardcode unpinned, resource-less
# busybox test Pods, and standalone PostgreSQL ignores existing Secret credentials.
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
app=$root/apps/homelab/litellm
render=$(mktemp)
trap 'rm -f "$render"' EXIT

test -f "$app/Chart.yaml" || { echo 'LiteLLM first-party chart is missing' >&2; exit 1; }
test "$(yq -r '.name' "$app/Chart.yaml")" = litellm-wrapper
test "$(yq -r '.dependencies // [] | length' "$app/Chart.yaml")" = 0
if test -f "$root/clusters/kepler/apps/litellm.yaml"; then
  test "$(yq -r '.spec.project' "$root/clusters/kepler/apps/litellm.yaml")" = homelab
else
  echo 'Skipping Application assertion: clusters/kepler/apps/litellm.yaml is orchestrator-owned and not present yet.'
fi

helm template litellm "$app" >"$render"
if grep -Eq '^kind: ClusterRole(Binding)?$' "$render"; then
  echo 'LiteLLM rendered cluster-wide RBAC outside the platform lane' >&2
  exit 1
fi
if grep -Ei '100\.76\.140\.121|192\.168\.10\.230|postgres\.discovery|minio.*kepler' "$render"; then
  echo 'LiteLLM rendered a legacy data-service endpoint' >&2
  exit 1
fi
python3 "$root/scripts/check-first-wave-resources.py" --forbid-gpu "$render"
python3 - "$render" <<'PY'
import re
import sys

import yaml

with open(sys.argv[1]) as stream:
    documents = [doc for doc in yaml.safe_load_all(stream) if doc]
by_kind_name = {(doc["kind"], doc["metadata"]["name"]): doc for doc in documents}
containers = []
for doc in documents:
    assert doc["kind"] not in {"Ingress", "HorizontalPodAutoscaler", "ScaledObject"}
    spec = doc.get("spec", {})
    if doc["kind"] == "CronJob":
        spec = spec["jobTemplate"]["spec"]
    pod = spec if doc["kind"] == "Pod" else spec.get("template", {}).get("spec", {})
    for container in pod.get("initContainers", []) + pod.get("containers", []):
        containers.append(container)
        image = container["image"]
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), f"mutable image: {image}"
        assert ":latest" not in image
        for boundary in ("requests", "limits"):
            for resource in ("cpu", "memory"):
                assert container["resources"][boundary][resource]
    for claim in spec.get("volumeClaimTemplates", []):
        assert claim["spec"]["storageClassName"] == "local-path"
        assert claim["spec"]["resources"]["requests"]["storage"] == "10Gi"
assert len(containers) == 2

deployment = by_kind_name["Deployment", "litellm"]
assert deployment["spec"]["replicas"] == 1
container = deployment["spec"]["template"]["spec"]["containers"][0]
assert container["image"].startswith("ghcr.io/berriai/litellm-database:")
env = {entry["name"]: entry for entry in container["env"]}
for key in ("LITELLM_MASTER_KEY", "LITELLM_SALT_KEY", "POSTGRES_PASSWORD"):
    assert env[key]["valueFrom"]["secretKeyRef"] == {"name": "litellm-secrets", "key": key}
assert env["DATABASE_URL"]["value"] == "postgresql://litellm:$(POSTGRES_PASSWORD)@postgres.litellm.svc.cluster.local:5432/litellm"
names = [entry["name"] for entry in container["env"]]
assert names.index("POSTGRES_PASSWORD") < names.index("DATABASE_URL")
for probe, path in (("startupProbe", "/health/readiness"), ("readinessProbe", "/health/readiness"), ("livenessProbe", "/health/liveliness")):
    assert container[probe]["httpGet"]["path"] == path
service = by_kind_name["Service", "litellm"]["spec"]
assert service["type"] == "ClusterIP"
assert service["ports"][0]["port"] == 4000
assert service["selector"] == deployment["spec"]["selector"]["matchLabels"]

secrets = [doc for doc in documents if doc["kind"] == "ExternalSecret"]
assert secrets
for secret in secrets:
    spec = secret["spec"]
    assert spec["secretStoreRef"] == {"kind": "ClusterSecretStore", "name": "vault-homelab"}
    assert spec["refreshInterval"] == "1h"
    assert spec["target"]["name"] == "litellm-secrets"
    assert {entry["secretKey"] for entry in spec["data"]} == {"LITELLM_MASTER_KEY", "LITELLM_SALT_KEY", "POSTGRES_PASSWORD"}
    for entry in spec["data"]:
        assert entry["remoteRef"] == {"key": "lab/litellm", "property": entry["secretKey"]}

config = yaml.safe_load(by_kind_name["ConfigMap", "litellm-config"]["data"]["config.yaml"])
assert config["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"
assert config["general_settings"]["database_url"] == "os.environ/DATABASE_URL"
assert len(config["model_list"]) == 1
assert config["model_list"][0]["litellm_params"]["mock_response"]
assert config["router_settings"] == {}
assert by_kind_name["NetworkPolicy", "default-deny"]["spec"]["policyTypes"] == ["Ingress", "Egress"]
print("LiteLLM contract passed")
PY
