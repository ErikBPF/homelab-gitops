#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
app=$root/apps/homelab/openwebui
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
render=$work/render.yaml

test "$(yq -r '.apiVersion' "$app/Chart.yaml")" = v2
test "$(yq -r '.name' "$app/Chart.yaml")" = openwebui-wrapper
test "$(yq -r '.version' "$app/Chart.yaml")" = 1.0.0
test "$(yq -r '.dependencies | length' "$app/Chart.yaml")" = 1
test "$(yq -r '.dependencies[0].name' "$app/Chart.yaml")" = open-webui
test "$(yq -r '.dependencies[0].repository' "$app/Chart.yaml")" = https://open-webui.github.io/helm-charts
test "$(yq -r '.dependencies[0].version' "$app/Chart.yaml")" = 16.5.0
if test -f "$root/clusters/kepler/apps/openwebui.yaml"; then
  test "$(yq -r '.spec.project' "$root/clusters/kepler/apps/openwebui.yaml")" = homelab
else
  echo 'Open WebUI Argo Application is orchestrator-owned; skipping absent Application'
fi

# Build in isolation: never leave Chart.lock or downloaded charts in the worktree.
cp -R "$app" "$work/openwebui"
helm dependency build "$work/openwebui" >/dev/null
helm template openwebui "$work/openwebui" --namespace openwebui >"$render"
python3 "$root/scripts/check-first-wave-resources.py" "$render"
python3 - "$render" <<'PY'
import re
import sys

import yaml

with open(sys.argv[1]) as stream:
    documents = [doc for doc in yaml.safe_load_all(stream) if doc]

for doc in documents:
    assert doc["kind"] not in {"ClusterRole", "ClusterRoleBinding", "HorizontalPodAutoscaler", "ScaledObject", "ScaledJob", "Certificate"}, doc["kind"]
    spec = doc.get("spec", {})
    if doc["kind"] == "CronJob":
        spec = spec.get("jobTemplate", {}).get("spec", {})
    pod = spec if doc["kind"] == "Pod" else spec.get("template", {}).get("spec", {})
    for container in pod.get("initContainers", []) + pod.get("containers", []):
        image = container.get("image", "")
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), f"mutable image: {image}"
        for boundary in ("requests", "limits"):
            for resource in ("cpu", "memory"):
                assert container.get("resources", {}).get(boundary, {}).get(resource), (container["name"], boundary, resource)

workloads = [doc for doc in documents if doc["kind"] in {"Deployment", "StatefulSet"}]
assert len(workloads) == 1, "unexpected helper workload"
workload = workloads[0]
assert workload["spec"]["replicas"] == 1
pod = workload["spec"]["template"]["spec"]
assert len(pod["containers"]) == 1
container = pod["containers"][0]
env = {entry["name"]: entry for entry in container["env"]}
assert len(env) == len(container["env"]), "duplicate environment variables"
expected = {
    "WEBUI_URL": "http://openwebui.homelab.pastelariadev.com",
    "ENABLE_OAUTH": "true",
    "ENABLE_OAUTH_SIGNUP": "true",
    "ENABLE_FORWARD_USER_INFO_HEADERS": "true",
    "OPENID_PROVIDER_URL": "https://authentik.homelab.pastelariadev.com/application/o/openwebui/.well-known/openid-configuration",
    "OAUTH_PROVIDER_NAME": "Authentik",
    "OAUTH_SCOPES": "openid email profile",
    "OPENID_REDIRECT_URI": "http://openwebui.homelab.pastelariadev.com/oauth/oidc/callback",
    "OPENAI_API_BASE_URL": "http://litellm.litellm.svc.cluster.local:4000/v1",
    "DEFAULT_MODELS": "spike-echo",
    "DEFAULT_USER_ROLE": "user",
}
for name, value in expected.items():
    actual = env[name].get("value")
    assert isinstance(actual, str), (name, actual)
    assert (actual.lower() == value if value == "true" else actual == value), (name, actual)

secrets = [doc for doc in documents if doc["kind"] == "ExternalSecret"]
assert len(secrets) == 1
secret = secrets[0]
assert secret["apiVersion"] == "external-secrets.io/v1"
assert secret["spec"]["refreshInterval"] == "1h"
assert secret["spec"]["secretStoreRef"] == {"kind": "ClusterSecretStore", "name": "vault-homelab"}
keys = {"OAUTH_CLIENT_ID", "OAUTH_CLIENT_SECRET", "WEBUI_SECRET_KEY", "OPENAI_API_KEY"}
assert {entry["secretKey"] for entry in secret["spec"]["data"]} == keys
for entry in secret["spec"]["data"]:
    key = entry["secretKey"]
    assert entry["remoteRef"] == {"key": "lab/openwebui", "property": key}
    assert "value" not in env[key], f"inline credential: {key}"
    assert env[key]["valueFrom"]["secretKeyRef"] == {"name": secret["spec"]["target"]["name"], "key": key}

ingresses = [doc for doc in documents if doc["kind"] == "Ingress"]
assert len(ingresses) == 1
ingress = ingresses[0]["spec"]
assert ingress["ingressClassName"] == "traefik"
assert "tls" not in ingress
assert ingress["rules"][0]["host"] == "openwebui.homelab.pastelariadev.com"
path = ingress["rules"][0]["http"]["paths"][0]
assert path["path"] == "/" and path["pathType"] == "Prefix"
services = [doc for doc in documents if doc["kind"] == "Service"]
assert len(services) == 1 and services[0]["spec"]["type"] == "ClusterIP"
assert path["backend"]["service"]["name"] == services[0]["metadata"]["name"]

pvcs = [doc for doc in documents if doc["kind"] == "PersistentVolumeClaim"]
assert len(pvcs) == 1
pvc = pvcs[0]
assert pvc["spec"]["storageClassName"] == "local-path"
assert pvc["spec"]["resources"]["requests"]["storage"] == "5Gi"
assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"]
mount = next(mount for mount in container["volumeMounts"] if mount["mountPath"] == "/app/backend/data")
volume = next(volume for volume in pod["volumes"] if volume["name"] == mount["name"])
assert volume["persistentVolumeClaim"]["claimName"] == pvc["metadata"]["name"]

policies = {doc["metadata"]["name"]: doc for doc in documents if doc["kind"] == "NetworkPolicy"}
deny = policies["default-deny"]
assert deny["metadata"]["namespace"] == "openwebui"
assert deny["spec"] == {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}
runtime = policies["allow-runtime"]["spec"]
assert runtime["ingress"] == [{"from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "traefik"}}}], "ports": [{"protocol": "TCP", "port": 8080}]}]
for namespace, ports in (("litellm", [("TCP", 4000)]), ("mcp-echo", [("TCP", 8080)]), ("kube-system", [("UDP", 53), ("TCP", 53)])):
    assert {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": namespace}}}], "ports": [{"protocol": protocol, "port": port} for protocol, port in ports]} in runtime["egress"], namespace
assert {"to": [{"ipBlock": {"cidr": "192.168.10.210/32"}}], "ports": [{"protocol": "TCP", "port": 443}]} in runtime["egress"]
assert len(runtime["egress"]) == 4, "unexpected egress allowance"
print("Open WebUI contract passed")
PY
