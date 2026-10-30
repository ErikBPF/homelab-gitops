"""Assert the disposable Alloy payload boundary before a VM can start."""

import copy
import hashlib
import json
import pathlib
import re
import sys

import yaml

IMAGE = "docker.io/grafana/alloy@sha256:754409730f1a4ed9781f8a2ea3b6a8c55750ee125a267ecf8fb449f9a25c109a"
SINK = "http://alloy-replica-receiver.monitoring.svc.cluster.local:9090/api/v1/write"


def scrape(config, name):
    matches = re.findall(
        f'^prometheus\\.scrape "{name}" \\{{\\n.*?^\\}}',
        config,
        re.MULTILINE | re.DOTALL,
    )
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one {name} scrape")
    return matches[0]


def check_payload(documents, source_config, run_id):
    if not re.fullmatch("[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", run_id):
        raise AssertionError("invalid run ID")
    expected_resources = {
        ("ServiceAccount", "monitoring", "alloy-metrics"),
        ("ConfigMap", "monitoring", "alloy-metrics"),
        ("ClusterRole", "", "alloy-metrics-nodes-proxy"),
        ("ClusterRoleBinding", "", "alloy-metrics-nodes-proxy"),
        ("Role", "traefik", "alloy-metrics"),
        ("Role", "argocd", "alloy-metrics"),
        ("RoleBinding", "traefik", "alloy-metrics"),
        ("RoleBinding", "argocd", "alloy-metrics"),
        ("Service", "monitoring", "alloy-metrics"),
        ("Deployment", "monitoring", "alloy-metrics"),
    }
    identities = [
        (item["kind"], item["metadata"].get("namespace", ""), item["metadata"]["name"])
        for item in documents
    ]
    if not (
        len(identities) == len(expected_resources)
        and set(identities) == expected_resources
    ):
        raise AssertionError("unexpected resource identity or duplicate")
    serialized = json.dumps(documents)
    for forbidden in [
        "pastelariadev.com",
        "10.250.0.",
        "monitoring-kube-prometheus-prometheus",
        "kube-state-metrics.monitoring",
    ]:
        if forbidden in serialized:
            raise AssertionError(f"production endpoint survives: {forbidden}")
    deployments = [item for item in documents if item["kind"] == "Deployment"]
    if len(deployments) != 1:
        raise AssertionError("expected one Alloy deployment")
    pod = deployments[0]["spec"]["template"]["spec"]
    if pod["serviceAccountName"] != "alloy-metrics":
        raise AssertionError("wrong scraper identity")
    if len(pod["containers"]) != 1:
        raise AssertionError("unexpected sidecar")
    if pod["containers"][0]["image"] != IMAGE:
        raise AssertionError("unreviewed image")
    if pod["containers"][0]["imagePullPolicy"] != "Never":
        raise AssertionError("runtime image pull permitted")
    if pod.get("imagePullSecrets"):
        raise AssertionError("registry credentials present")
    configs = [
        item["data"]["config.alloy"]
        for item in documents
        if item["kind"] == "ConfigMap" and "config.alloy" in item.get("data", {})
    ]
    if len(configs) != 1:
        raise AssertionError("expected one Alloy configuration")
    config = configs[0]
    for name in ["cadvisor", "kubelet"]:
        if scrape(config, name) != scrape(source_config, name):
            raise AssertionError(f"{name} scrape changed")
    if re.findall('url\\s*=\\s*"([^"]+)"', config) != [SINK]:
        raise AssertionError("unknown remote-write destination")
    if re.findall('"__address__"\\s*=\\s*"([^"]+)"', config) != ["127.0.0.1:12345"]:
        raise AssertionError("unknown static target")
    if re.findall('replacement\\s*=\\s*"([^"\\n]+:443)"', config) != [
        "kubernetes.default.svc:443",
        "kubernetes.default.svc:443",
    ]:
        raise AssertionError("unknown API endpoint")
    if f'external_labels = {{ "cluster" = "{run_id}" }}' not in config:
        raise AssertionError("run ID absent from data path")
    components = re.findall('^([a-z][a-z_.]+) "([a-z_]+)" \\{', config, re.MULTILINE)
    expected = [
        ("discovery.kubernetes", "nodes"),
        ("discovery.relabel", "cadvisor"),
        ("prometheus.scrape", "cadvisor"),
        ("discovery.relabel", "kubelet"),
        ("prometheus.scrape", "kubelet"),
        ("prometheus.relabel", "kubelet_keep"),
        ("discovery.kubernetes", "traefik"),
        ("discovery.relabel", "traefik"),
        ("prometheus.scrape", "traefik"),
        ("discovery.kubernetes", "argocd"),
        ("discovery.relabel", "argocd"),
        ("prometheus.scrape", "argocd"),
        ("prometheus.relabel", "argocd_keep"),
        ("prometheus.scrape", "alloy"),
        ("prometheus.relabel", "alloy_keep"),
        ("prometheus.remote_write", "discovery"),
    ]
    if components != expected:
        raise AssertionError("unknown or missing component")
    expected_config = source_config
    for component, name in [
        ("prometheus.scrape", "ksm"),
        ("prometheus.scrape", "etcd"),
        ("prometheus.relabel", "etcd_keep"),
    ]:
        expected_config = re.sub(
            f'^{re.escape(component)} "{name}" \\{{\\n.*?^\\}}\\n',
            "",
            expected_config,
            flags=re.MULTILINE | re.DOTALL,
        )
    expected_config = expected_config.replace(
        "http://monitoring-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090/api/v1/write",
        SINK,
    )
    expected_config = expected_config.replace(
        'external_labels = { "cluster" = "homelab" }',
        f'external_labels = {{ "cluster" = "{run_id}" }}',
    )
    if config != expected_config.removesuffix("\n"):
        raise AssertionError(
            "configuration differs from the complete reviewed transformation"
        )

    annotations = deployments[0]["spec"]["template"]["metadata"]["annotations"]
    # The pinned chart hashes the unchomped config and truncates to 63 characters.
    expected_checksum = hashlib.sha256(expected_config.encode()).hexdigest()[:63]
    if annotations.get("checksum/config") != expected_checksum:
        raise AssertionError("configuration checksum mismatch")
    normalized_documents = copy.deepcopy(documents)
    deployment = next(
        item for item in normalized_documents if item["kind"] == "Deployment"
    )
    deployment["spec"]["template"]["metadata"]["annotations"][
        "checksum/config"
    ] = "REPLICA_CONFIG_CHECKSUM"
    normalized = json.dumps(
        normalized_documents, sort_keys=True, separators=(",", ":")
    ).replace(run_id, "REPLICA_RUN_ID")
    # This reviewed whole-manifest pin also rejects added env, arguments and mounts.
    if (
        hashlib.sha256(normalized.encode()).hexdigest()
        != "9bca91b7509883ca6aab2076801cab2009cac91d2a25687abd63a142a8f3d1f7"
    ):
        raise AssertionError("complete rendered payload changed")


if __name__ == "__main__":
    manifest, source, run_id = sys.argv[1:]
    docs = [
        item for item in yaml.safe_load_all(pathlib.Path(manifest).read_text()) if item
    ]
    config = yaml.safe_load(pathlib.Path(source).read_text())["alloy"]["alloy"][
        "configMap"
    ]["content"]
    check_payload(docs, config, run_id)
    print("ALLOY_REPLICA_PAYLOAD_OK")
