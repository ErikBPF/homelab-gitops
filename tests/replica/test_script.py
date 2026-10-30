import base64
import hashlib
import json
import os
import shlex
from pathlib import Path


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def write_guest(path, value):
    encoded = base64.b64encode(value.encode()).decode()
    server.succeed(
        f"printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(path)}"
    )


def apply(objects, name):
    path = f"/run/alloy-replica/{name}.json"
    write_guest(
        path, json.dumps({"apiVersion": "v1", "kind": "List", "items": objects})
    )
    server.succeed(f"k3s kubectl apply -f {path}")


def probe(path, status, pointer=None, value=None, contains=None):
    args = [
        "python3",
        f"{REPLICA}/guest_probe.py",
        "--kubeconfig",
        "/run/alloy-replica/identity.json",
        "--path",
        path,
        "--expect-status",
        str(status),
    ]
    if pointer is not None:
        args += ["--json-pointer", pointer, "--expect-value", value]
    if contains is not None:
        args += ["--body-contains", contains]
    return shlex.join(args)


def metric_command():
    code = r"""
import json, sys, urllib.parse, urllib.request

run_id = sys.argv[1]
nodes = set(json.loads(sys.argv[2]))
for name in [
    "container_cpu_usage_seconds_total",
    "container_memory_working_set_bytes",
    "kubelet_node_name",
    "kubelet_running_pods",
    "traefik_replica_metric",
    "argocd_app_info",
]:
    query = name + '{cluster="' + run_id + '"}'
    url = "http://127.0.0.1:9090/api/v1/query?" + urllib.parse.urlencode(
        {"query": query}
    )
    with urllib.request.urlopen(url, timeout=5) as response:
        result = json.load(response)
    if result.get("status") != "success":
        raise RuntimeError("query failed")
    samples = result["data"]["result"]
    if not samples:
        raise RuntimeError("missing series " + name)
    if (
        name.startswith(("container_", "kubelet_"))
        and {s["metric"].get("node") for s in samples} != nodes
    ):
        raise RuntimeError("missing node series " + name)
    if name in ["traefik_replica_metric", "argocd_app_info"] and not all(
        s["metric"].get("replica_run") == run_id for s in samples
    ):
        raise RuntimeError("wrong synthetic discovery series")
print("FRESH_REPLICA_SERIES_OK")
"""
    return shlex.join(["python3", "-c", code, RUN_ID, json.dumps(node_names)])


completed = False
try:
    payload = json.loads(Path(PAYLOAD, "payload.json").read_text())
    pins = json.loads(Path(REPLICA, "artifacts.json").read_text())
    require(payload["run_id"] == RUN_ID, "payload belongs to another run")
    for name, digest in payload["files"].items():
        require(
            hashlib.sha256(Path(PAYLOAD, name).read_bytes()).hexdigest() == digest,
            "payload digest mismatch",
        )
    start_all()
    server.wait_for_unit("k3s")
    agent.wait_for_unit("k3s")
    server.wait_until_succeeds(
        "test $(k3s kubectl get nodes --no-headers | grep -c ' Ready ') -eq 2",
        timeout=300,
    )
    server.wait_until_succeeds(
        "k3s kubectl -n kube-system wait --for=condition=Ready pod -l k8s-app=kube-dns --timeout=30s",
        timeout=300,
    )
    node_objects = json.loads(server.succeed("k3s kubectl get nodes -o json"))["items"]
    node_names = sorted(item["metadata"]["name"] for item in node_objects)
    require(len(node_names) == 2, "wrong replica node count")
    for machine in [server, agent]:
        machine.succeed(
            f"k3s ctr --namespace k8s.io images import --local "
            f"--base-name docker.io/grafana/alloy --digests {ARCHIVE}"
        )
        inspection = json.loads(
            machine.wait_until_succeeds(
                "k3s crictl --config /dev/null "
                "--runtime-endpoint unix:///run/k3s/containerd/containerd.sock "
                "--image-endpoint unix:///run/k3s/containerd/containerd.sock "
                "inspecti --quiet --output json " + shlex.quote(payload["image"]),
                timeout=30,
            )
        )["status"]
        require(
            inspection.get("id") == pins["image_config_digest"],
            "replica image configuration mismatch",
        )
        require(
            payload["image"] in inspection.get("repoDigests", []),
            "replica image reference unresolved",
        )
    server.succeed("install -d -m 0700 /run/alloy-replica")
    for namespace in ["monitoring", "traefik", "argocd", "replica-denied"]:
        server.succeed(f"k3s kubectl create namespace {namespace}")
    apply(
        [
            {
                "apiVersion": "v1",
                "kind": "ServiceAccount",
                "metadata": {"name": "alloy-metrics", "namespace": "monitoring"},
            }
        ],
        "service-account",
    )
    server.succeed(f"k3s kubectl apply -f {PAYLOAD}/legacy-rbac.yaml")
    token_setup = r"""
import base64, json, os, subprocess

admin = json.loads(
    subprocess.check_output(["k3s", "kubectl", "config", "view", "--raw", "-o", "json"])
)
cluster = admin["clusters"][0]["cluster"]
if cluster["server"] != "https://127.0.0.1:6443":
    raise RuntimeError("unexpected replica API")
ca = "/run/alloy-replica/ca.crt"
with open(ca, "wb") as f:
    f.write(base64.b64decode(cluster["certificate-authority-data"]))
token = subprocess.check_output(
    [
        "k3s",
        "kubectl",
        "-n",
        "monitoring",
        "create",
        "token",
        "alloy-metrics",
        "--duration=20m",
    ],
    text=True,
).strip()
config = {
    "apiVersion": "v1",
    "kind": "Config",
    "clusters": [
        {
            "name": "replica",
            "cluster": {"server": cluster["server"], "certificate-authority": ca},
        }
    ],
    "users": [{"name": "scraper", "user": {"token": token}}],
    "contexts": [
        {"name": "replica", "context": {"cluster": "replica", "user": "scraper"}}
    ],
    "current-context": "replica",
}
fd = os.open(
    "/run/alloy-replica/identity.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
)
with os.fdopen(fd, "w") as f:
    json.dump(config, f)
"""
    server.succeed(shlex.join(["python3", "-c", token_setup]))
    canary = "replica-" + RUN_ID
    secret_value = base64.b64encode(canary.encode()).decode()
    apply(
        [
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "replica-canary", "namespace": "monitoring"},
                "data": {"canary": canary},
            },
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "replica-canary", "namespace": "monitoring"},
                "data": {"canary": secret_value},
            },
            {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "replica-canary", "namespace": "monitoring"},
                "spec": {
                    "automountServiceAccountToken": False,
                    "containers": [
                        {
                            "name": "canary",
                            "image": payload["image"],
                            "imagePullPolicy": "Never",
                            "command": ["/bin/sh", "-c", f"echo {canary}; sleep 1800"],
                        }
                    ],
                },
            },
        ],
        "canaries",
    )
    server.succeed(
        "k3s kubectl -n monitoring wait --for=condition=Ready pod/replica-canary --timeout=180s"
    )
    services = [
        ("monitoring", "alloy-replica-receiver", 9090, "http"),
        ("traefik", "traefik-replica-metrics", 9100, "metrics"),
        ("argocd", "argocd-application-controller-metrics", 8082, "http-metrics"),
        ("replica-denied", "replica-denied", 9100, "metrics"),
    ]
    objects = []
    for namespace, name, port, port_name in services:
        metadata = {"name": name, "namespace": namespace}
        objects += [
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": metadata,
                "spec": {
                    "ports": [{"name": port_name, "port": port, "targetPort": port}]
                },
            },
            {
                "apiVersion": "v1",
                "kind": "Endpoints",
                "metadata": metadata,
                "subsets": [
                    {
                        "addresses": [{"ip": SERVER_IP}],
                        "ports": [{"name": port_name, "port": port}],
                    }
                ],
            },
        ]
    apply(objects, "local-services")
    checks = [
        (
            "/api/v1/namespaces/monitoring/secrets/replica-canary",
            "/data/canary",
            secret_value,
            None,
        ),
        (
            "/api/v1/namespaces/monitoring/configmaps/replica-canary",
            "/data/canary",
            canary,
            None,
        ),
        ("/api/v1/namespaces/monitoring/pods/replica-canary/log", None, None, canary),
        (
            "/api/v1/namespaces/replica-denied/services/replica-denied",
            "/metadata/name",
            "replica-denied",
            None,
        ),
    ]
    for path, pointer, value, contains in checks:
        server.succeed(probe(path, 200, pointer, value, contains))
        server.fail(probe(path, 403))
    server.succeed(f"k3s kubectl apply -f {PAYLOAD}/current.yaml")
    for path, pointer, value, contains in checks:
        server.succeed(probe(path, 200, pointer, value, contains))
        server.fail(probe(path, 403))
    server.succeed(
        f"k3s kubectl delete -f {PAYLOAD}/legacy-rbac.yaml --wait=true --timeout=30s"
    )
    for path, _pointer, _value, _contains in checks:
        server.succeed(probe(path, 403))
    for namespace, name, _port, _port_name in services[1:3]:
        server.succeed(
            probe(
                f"/api/v1/namespaces/{namespace}/services/{name}",
                200,
                "/metadata/name",
                name,
            )
        )
    for name in node_names:
        server.succeed(probe(f"/api/v1/nodes/{name}", 200, "/metadata/name", name))
    write_guest(
        "/run/alloy-replica/prometheus.yaml",
        "global:\n  scrape_interval: 1m\nscrape_configs: []\n",
    )
    server.succeed(
        "systemd-run --unit=alloy-replica-receiver prometheus --config.file=/run/alloy-replica/prometheus.yaml --web.listen-address=0.0.0.0:9090 --web.enable-remote-write-receiver --storage.tsdb.path=/run/alloy-replica/tsdb"
    )
    server.succeed(
        f"systemd-run --unit=alloy-replica-exporters python3 {REPLICA}/fixture_exporters.py {RUN_ID}"
    )
    server.succeed(
        "k3s kubectl -n monitoring rollout status deployment/alloy-metrics --timeout=180s"
    )
    pods = json.loads(
        server.succeed(
            "k3s kubectl -n monitoring get pods -l app.kubernetes.io/instance=alloy-metrics -o json"
        )
    )["items"]
    require(len(pods) == 1, "expected one scraper pod")
    statuses = pods[0]["status"]["containerStatuses"]
    require(len(statuses) == 1, "unexpected scraper container")
    image_id = statuses[0]["imageID"]
    require(
        image_id.endswith(payload["image"].split("@", 1)[1]),
        "runtime image digest differs",
    )
    version = server.succeed(
        "k3s kubectl -n monitoring exec deployment/alloy-metrics -c alloy -- /bin/alloy --version"
    ).strip()
    write_guest("/run/alloy-replica/version.txt", version)
    version_check = "import pathlib,sys; sys.path.insert(0,sys.argv[1]); from tls_fixture import check_version; check_version(pathlib.Path('/run/alloy-replica/version.txt').read_text(),sys.argv[2])"
    server.succeed(
        shlex.join(["python3", "-c", version_check, REPLICA, pins["image_version"]])
    )
    server.wait_until_succeeds(metric_command(), timeout=180)
    write_guest("/run/alloy-replica/run-id", RUN_ID)
    require(
        server.succeed("cat /run/alloy-replica/run-id").strip() == RUN_ID,
        "run ID did not execute",
    )
    receipt = {
        "proof": "permissions",
        "run_id": RUN_ID,
        "nodes": node_names,
        "image": payload["image"],
        "alloy_version": version,
        "runtime_image_id": image_id,
        "payload": payload["files"],
        "legacy_controls": "all four real reads returned 200 and denial checks failed before and after apply without pruning",
        "pruned_controls": "all four real reads returned Kubernetes Forbidden 403",
        "series": "required fresh node and synthetic discovery series observed",
        "nodes_proxy": "retained; indirect operation denial not claimed",
        "slice_b_tls": "not run",
    }
    completed = True
finally:
    try:
        agent.shutdown()
    finally:
        server.shutdown()
if completed:
    receipt["cleanup"] = "both guests stopped"
    Path(os.environ["out"], "alloy-replica.json").write_text(
        json.dumps(receipt, indent=2) + "\n"
    )
    print("ALLOY_REPLICA_PERMISSIONS_METRICS_OK")
