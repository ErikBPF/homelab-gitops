import base64
import hashlib
import json
import os
import shlex
import time
from pathlib import Path


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def phase_remaining(deadline):
    remaining = deadline - time.monotonic()
    require(remaining > 0, "TLS phase deadline exceeded")
    return remaining


def phase_succeed(command, deadline):
    output = server.succeed(command, timeout=phase_remaining(deadline))
    phase_remaining(deadline)
    return output


def phase_observe(command, deadline):
    while True:
        status, output = server.execute(command, timeout=phase_remaining(deadline))
        phase_remaining(deadline)
        if status == 0:
            return output
        time.sleep(min(1, phase_remaining(deadline)))


def write_guest(path, value, deadline=None):
    encoded = base64.b64encode(value.encode()).decode()
    command = f"printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(path)}"
    if deadline is None:
        server.succeed(command)
    else:
        phase_succeed(command, deadline)


def observe_command(phase, pod_ip, started):
    code = r"""
import json, sys, urllib.parse, urllib.request
sys.path.insert(0, sys.argv[1])
from tls_fixture import METRICS, SCRAPES, target_statuses, validate_evidence

phase, run_id, pod_ip, started = sys.argv[2:]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(url):
    with opener.open(url, timeout=5) as response:
        if response.status != 200:
            raise AssertionError("observation HTTP failed")
        return json.load(response)

try:
    witness = get("http://127.0.0.1:9446/state")
    components = {
        scrape: get("http://" + pod_ip + ":12345/api/v0/web/components/prometheus.scrape." + scrape)
        for scrape in SCRAPES
    }
    query = '{cluster="' + run_id + '",tls_phase="' + phase + '"}'
    metrics = get("http://127.0.0.1:9090/api/v1/query?" + urllib.parse.urlencode({"query": query}))
    if metrics.get("status") != "success" or metrics["data"].get("resultType") != "vector":
        raise AssertionError("metrics query failed")
    samples = [row["metric"] for row in metrics["data"]["result"]]
    validate_evidence(phase, run_id, float(started), witness, components, samples, insecure=phase == "mutation")
    rejected = False
    if phase == "mutation":
        try:
            validate_evidence(phase, run_id, float(started), witness, components, samples)
        except AssertionError as error:
            if str(error) != "untrusted endpoint received HTTP":
                raise
            rejected = True
        if not rejected:
            raise AssertionError("insecure mutation escaped denial validator")
    statuses = {scrape: target_statuses(components[scrape], scrape, float(started)) for scrape in SCRAPES}
    allowed_labels = {"__name__", "cluster", "tls_phase", "tls_scrape", "tls_case", "node", "instance", "job"}
    metric_names = {name for names in METRICS.values() for name in names}
    endpoint_samples = [
        {"metric": {key: value for key, value in row["metric"].items() if key in allowed_labels},
         "value": row["value"]}
        for row in metrics["data"]["result"] if row["metric"].get("__name__") in metric_names
    ]
    print(json.dumps({"phase": phase, "started": float(started), "witness": witness,
                      "target_statuses": statuses, "endpoint_samples": endpoint_samples,
                      "insecure_denial_validator_failed": rejected}))
except Exception:
    print("TLS_REPLICA_EVIDENCE_INCOMPLETE", file=sys.stderr)
    raise SystemExit(1)
"""
    return shlex.join(
        ["python3", "-c", code, REPLICA, phase, RUN_ID, pod_ip, str(started)]
    )


completed = False
try:
    payload = json.loads(Path(PAYLOAD, "payload.json").read_text())
    pins = json.loads(Path(REPLICA, "artifacts.json").read_text())
    require(SERVER_IP == "192.168.1.2", "unreviewed replica server address")
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
    nodes = json.loads(server.succeed("k3s kubectl get nodes -o json"))["items"]
    node_names = sorted(item["metadata"]["name"] for item in nodes)
    require(len(node_names) == 2, "wrong replica node count")
    for machine in [server, agent]:
        machine.succeed(
            f"k3s ctr --namespace k8s.io images import --local --base-name docker.io/grafana/alloy --digests {ARCHIVE}"
        )
        status = json.loads(
            machine.wait_until_succeeds(
                "k3s crictl --config /dev/null --runtime-endpoint unix:///run/k3s/containerd/containerd.sock "
                "--image-endpoint unix:///run/k3s/containerd/containerd.sock inspecti --quiet --output json "
                + shlex.quote(payload["image"]),
                timeout=30,
            )
        )["status"]
        require(
            status.get("id") == pins["image_config_digest"],
            "image configuration mismatch",
        )
        require(
            payload["image"] in status.get("repoDigests", []),
            "image reference unresolved",
        )
    server.succeed("install -d -m 0700 /run/alloy-replica")
    server.succeed("k3s kubectl create namespace monitoring")
    server.succeed(f"python3 {REPLICA}/tls_fixture.py material /run/alloy-replica/tls")
    # Generate and transfer only a synthetic, unprivileged token inside the guest.
    identity_setup = r"""
import base64, json, pathlib, subprocess
root = pathlib.Path("/run/alloy-replica/tls")
secret = {"apiVersion": "v1", "kind": "Secret",
          "metadata": {"name": "alloy-tls-identity", "namespace": "monitoring"},
          "data": {name: base64.b64encode((root / name).read_bytes()).decode() for name in ["token", "ca.crt"]}}
subprocess.run(["k3s", "kubectl", "apply", "-f", "-"], input=json.dumps(secret), text=True, check=True,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
"""
    server.succeed(shlex.join(["python3", "-c", identity_setup]))
    receiver = [
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "alloy-replica-receiver", "namespace": "monitoring"},
            "spec": {"ports": [{"name": "http", "port": 9090, "targetPort": 9090}]},
        },
        {
            "apiVersion": "v1",
            "kind": "Endpoints",
            "metadata": {"name": "alloy-replica-receiver", "namespace": "monitoring"},
            "subsets": [
                {
                    "addresses": [{"ip": SERVER_IP}],
                    "ports": [{"name": "http", "port": 9090}],
                }
            ],
        },
    ]
    write_guest(
        "/run/alloy-replica/receiver.json",
        json.dumps({"apiVersion": "v1", "kind": "List", "items": receiver}),
    )
    server.succeed("k3s kubectl apply -f /run/alloy-replica/receiver.json")
    write_guest(
        "/run/alloy-replica/prometheus.yaml",
        "global:\n  scrape_interval: 1m\nscrape_configs: []\n",
    )
    server.succeed(
        "systemd-run --unit=alloy-replica-receiver prometheus --config.file=/run/alloy-replica/prometheus.yaml --web.listen-address=0.0.0.0:9090 --web.enable-remote-write-receiver --storage.tsdb.path=/run/alloy-replica/tsdb"
    )
    results = []
    versions = []
    image_ids = []
    for phase in ["secure", "mutation", "restored"]:
        server.succeed(
            f"systemd-run --unit=alloy-tls-{phase} python3 {REPLICA}/tls_fixture.py serve /run/alloy-replica/tls --phase {phase}"
        )
        ready = r"""
import json, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open("http://127.0.0.1:9446/ready", timeout=5) as response:
    state = json.load(response)
if len(state["targets"]) != 6 or any(any(row.values()) for row in state["targets"].values()):
    raise SystemExit("TLS witness counters are not fresh")
"""
        server.wait_until_succeeds(shlex.join(["python3", "-c", ready]), timeout=30)
        phase_deadline = time.monotonic() + 180
        started = float(phase_succeed("date +%s.%N", phase_deadline).strip())
        phase_succeed(
            f"k3s kubectl apply -f {PAYLOAD}/tls-{phase}.yaml", phase_deadline
        )
        phase_succeed(
            f"k3s kubectl -n monitoring wait --for=condition=Ready pod/alloy-tls-{phase} --timeout=120s",
            phase_deadline,
        )
        pod = json.loads(
            phase_succeed(
                f"k3s kubectl -n monitoring get pod alloy-tls-{phase} -o json",
                phase_deadline,
            )
        )
        statuses = pod["status"]["containerStatuses"]
        require(len(statuses) == 1, "unexpected TLS container")
        image_id = statuses[0]["imageID"]
        require(
            image_id.endswith(payload["image"].split("@", 1)[1]),
            "TLS image identity mismatch",
        )
        image_ids.append(image_id)
        version = phase_succeed(
            f"k3s kubectl -n monitoring exec alloy-tls-{phase} -- /bin/alloy --version",
            phase_deadline,
        ).strip()
        versions.append(version)
        write_guest("/run/alloy-replica/version.txt", version, phase_deadline)
        version_check = "import pathlib,sys; sys.path.insert(0,sys.argv[1]); from tls_fixture import check_version; check_version(pathlib.Path('/run/alloy-replica/version.txt').read_text(),sys.argv[2])"
        phase_succeed(
            shlex.join(
                ["python3", "-c", version_check, REPLICA, pins["image_version"]]
            ),
            phase_deadline,
        )
        observed = phase_observe(
            observe_command(phase, pod["status"]["podIP"], started), phase_deadline
        )
        results.append(json.loads(observed))
        phase_succeed(
            f"k3s kubectl -n monitoring delete pod alloy-tls-{phase} --wait=true --timeout=60s",
            phase_deadline,
        )
        phase_succeed(f"systemctl stop alloy-tls-{phase}", phase_deadline)
        results[-1]["elapsed_seconds"] = 180 - phase_remaining(phase_deadline)
    receipt = {
        "proof": "tls",
        "run_id": RUN_ID,
        "nodes": node_names,
        "image": payload["image"],
        "runtime_image_ids": image_ids,
        "alloy_versions": versions,
        "payload": payload["files"],
        "tls_matrix": results,
        "nodes_proxy": "unchanged; indirect operation denial not claimed",
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
    print("ALLOY_REPLICA_TLS_OK")
