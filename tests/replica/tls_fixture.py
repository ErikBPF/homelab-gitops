"""Local TLS witnesses and strict evidence checks for the disposable Alloy test."""

import argparse
import datetime
import http.server
import json
import os
import re
import secrets
import signal
import ssl
import subprocess
import threading
from pathlib import Path

SCRAPES = ("cadvisor", "kubelet")
CASES = ("trusted", "wrong-ca", "wrong-name")
PHASES = ("secure", "mutation", "restored")
HOST = "alloy-tls-witness.monitoring.svc.cluster.local"
METRICS = {
    "cadvisor": (
        "container_cpu_usage_seconds_total",
        "container_memory_working_set_bytes",
    ),
    "kubelet": ("kubelet_node_name", "kubelet_running_pods"),
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def check_version(output, expected):
    match = re.fullmatch(
        r"alloy, version (v\d+\.\d+\.\d+) \(branch: [^,]+, revision: [^)]+\)",
        output.splitlines()[0] if output else "",
    )
    require(match is not None and match[1] == expected, "Alloy binary version mismatch")
    return match[1]


def target_port(scrape, case):
    return 9440 + SCRAPES.index(scrape) * len(CASES) + CASES.index(case)


def target_path(scrape):
    return "/api/v1/nodes/tls-replica/proxy/metrics" + (
        "/cadvisor" if scrape == "cadvisor" else ""
    )


def target_url(scrape, case):
    return f"https://{HOST}:{target_port(scrape, case)}{target_path(scrape)}"


def tls_config(source, run_id, phase):
    require(phase in PHASES, "unknown TLS phase")
    require(
        re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", run_id)
        is not None,
        "invalid run ID",
    )

    def block(kind, name):
        matches = re.findall(
            r"^" + re.escape(kind) + ' "' + name + r'" \{\n.*?^\}',
            source,
            re.MULTILINE | re.DOTALL,
        )
        require(len(matches) == 1, "missing source component")
        return matches[0]

    output = []
    for scrape in SCRAPES:
        targets = []
        for case in CASES:
            labels = {
                "__address__": f"{HOST}:{target_port(scrape, case)}",
                "__metrics_path__": target_path(scrape),
                "node": "tls-replica",
                "tls_scrape": scrape,
                "tls_case": case,
                "tls_phase": phase,
            }
            targets.append(
                "    { "
                + ", ".join(
                    json.dumps(k) + " = " + json.dumps(v) for k, v in labels.items()
                )
                + " },"
            )
        output.append(
            f'discovery.relabel "{scrape}" {{\n  targets = [\n'
            + "\n".join(targets)
            + "\n  ]\n}"
        )
        actual = block("prometheus.scrape", scrape)
        if phase == "mutation":
            require(actual.count("tls_config {") == 1, "missing source TLS block")
            actual = actual.replace(
                "tls_config {", "tls_config {\n    insecure_skip_verify = true"
            )
        output.append(actual)
    output.append(block("prometheus.relabel", "kubelet_keep"))
    output.append(
        'prometheus.remote_write "discovery" {\n'
        '  endpoint { url = "http://alloy-replica-receiver.monitoring.svc.cluster.local:9090/api/v1/write" }\n'
        f'  external_labels = {{ "cluster" = "{run_id}" }}\n'
        "}\n"
    )
    return "\n\n".join(output)


def target_statuses(component, scrape, started):
    require(component.get("name") == "prometheus.scrape", "wrong component type")
    require(
        component.get("localID") == "prometheus.scrape." + scrape, "wrong component ID"
    )
    blocks = component.get("debugInfo")
    require(
        isinstance(blocks, list) and len(blocks) == len(CASES),
        "missing target observations",
    )
    result = {}
    for block in blocks:
        require(
            block.get("name") == "target" and block.get("type") == "block",
            "invalid target block",
        )
        body = block.get("body")
        require(isinstance(body, list), "invalid target attributes")
        attributes = {}
        for attr in body:
            name = attr.get("name")
            require(
                isinstance(name, str) and name not in attributes,
                "duplicate target attribute",
            )
            attributes[name] = attr
        values = {}
        for name in ("url", "health", "last_scrape", "last_error"):
            if name == "last_error" and name not in attributes:
                values[name] = ""
                continue
            attr = attributes.get(name, {})
            value = attr.get("value", {})
            require(
                attr.get("type") == "attr"
                and value.get("type") == "string"
                and isinstance(value.get("value"), str),
                "invalid typed target attribute",
            )
            values[name] = value["value"]
        try:
            observed = datetime.datetime.fromisoformat(
                values["last_scrape"].replace("Z", "+00:00")
            )
            require(
                observed.tzinfo is not None and observed.timestamp() >= started,
                "stale scrape observation",
            )
        except ValueError as error:
            raise AssertionError("invalid scrape timestamp") from error
        url = values["url"]
        require(url not in result, "duplicate target URL")
        result[url] = values
    require(
        set(result) == {target_url(scrape, case) for case in CASES},
        "unexpected target URLs",
    )
    return result


def validate_evidence(
    phase, run_id, started, witness, components, samples, *, insecure=False
):
    require(phase in PHASES and witness.get("phase") == phase, "wrong witness phase")
    keys = {scrape + "/" + case for scrape in SCRAPES for case in CASES}
    counters = witness.get("targets", {})
    require(
        set(counters) == keys and set(components) == set(SCRAPES), "missing witnesses"
    )
    require(isinstance(samples, list), "invalid metrics result")
    for sample in samples:
        require(
            sample.get("cluster") == run_id and sample.get("tls_phase") == phase,
            "stale metric labels",
        )
        require(
            sample.get("tls_scrape") in SCRAPES and sample.get("tls_case") in CASES,
            "unknown metric target",
        )
    for scrape in SCRAPES:
        statuses = target_statuses(components[scrape], scrape, started)
        for case in CASES:
            counts = counters[scrape + "/" + case]
            require(
                set(counts)
                == {
                    "attempts",
                    "tls_failures",
                    "http_requests",
                    "authenticated_requests",
                },
                "invalid witness counters",
            )
            require(
                all(type(value) is int and value >= 0 for value in counts.values()),
                "invalid witness count",
            )
            require(counts["attempts"] > 0, "no TLS attempt")
            status = statuses[target_url(scrape, case)]
            # Failed scrapes may emit internal series; these fixtures serve only METRICS.
            names = {
                sample.get("__name__")
                for sample in samples
                if sample.get("tls_scrape") == scrape
                and sample.get("tls_case") == case
                and sample.get("__name__") in METRICS[scrape]
            }
            if case == "trusted" or insecure:
                require(
                    counts["authenticated_requests"] > 0
                    and counts["http_requests"] >= counts["authenticated_requests"],
                    "positive HTTP control failed",
                )
                require(
                    status["health"] == "up" and not status["last_error"],
                    "positive scrape failed",
                )
                require(
                    set(METRICS[scrape]).issubset(names),
                    "positive fresh metrics missing",
                )
            else:
                require(
                    counts["tls_failures"] > 0
                    and counts["http_requests"]
                    == counts["authenticated_requests"]
                    == 0,
                    "untrusted endpoint received HTTP",
                )
                require(status["health"] == "down", "untrusted scrape succeeded")
                error = status["last_error"]
                reason = (
                    "certificate signed by unknown authority"
                    if case == "wrong-ca"
                    else "not kubernetes.default.svc"
                )
                require(
                    "tls: failed to verify certificate: x509:" in error
                    and reason in error,
                    "missing certificate verification error",
                )
                require(not names, "successful untrusted endpoint samples")


def create_material(root):
    root = Path(root)
    root.mkdir(mode=0o700)
    old_mask = os.umask(0o077)
    try:

        def openssl(*args):
            subprocess.run(
                ["openssl", *args],
                cwd=root,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )

        with (root / "token").open("x") as stream:
            stream.write(secrets.token_urlsafe(32))
        for name in ("ca", "other-ca"):
            openssl(
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=replica-" + name,
                "-keyout",
                name + ".key",
                "-out",
                name + ".crt",
            )
        for case in CASES:
            issuer = "other-ca" if case == "wrong-ca" else "ca"
            hostname = (
                "wrong.invalid" if case == "wrong-name" else "kubernetes.default.svc"
            )
            openssl(
                "req",
                "-new",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-subj",
                "/CN=" + hostname,
                "-keyout",
                case + ".key",
                "-out",
                case + ".csr",
            )
            (root / (case + ".ext")).write_text("subjectAltName=DNS:" + hostname + "\n")
            openssl(
                "x509",
                "-req",
                "-days",
                "1",
                "-in",
                case + ".csr",
                "-CA",
                issuer + ".crt",
                "-CAkey",
                issuer + ".key",
                "-CAcreateserial",
                "-extfile",
                case + ".ext",
                "-out",
                case + ".crt",
            )
    finally:
        os.umask(old_mask)


class Witness:
    def __init__(self, root, phase, *, bind="0.0.0.0", port_base=9440, admin_port=9446):
        require(phase in PHASES, "unknown TLS phase")
        self.phase = phase
        self.root = Path(root)
        self.token = (self.root / "token").read_text()
        self.lock = threading.Lock()
        self.counts = {}
        self.servers = {}
        self.threads = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                server = self.server
                with owner.lock:
                    owner.counts[server.key]["http_requests"] += 1
                    authenticated = (
                        self.headers.get("Authorization") == "Bearer " + owner.token
                    )
                    if authenticated:
                        owner.counts[server.key]["authenticated_requests"] += 1
                if not authenticated or self.path != target_path(server.scrape):
                    self.send_response(403)
                    self.end_headers()
                    return
                body = "".join(
                    f"{name} 1\n" for name in METRICS[server.scrape]
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class TLSServer(http.server.ThreadingHTTPServer):
            daemon_threads = True

            def get_request(self):
                connection, address = super().get_request()
                connection.settimeout(5)
                with owner.lock:
                    owner.counts[self.key]["attempts"] += 1
                try:
                    return (
                        self.context.wrap_socket(connection, server_side=True),
                        address,
                    )
                except ssl.SSLError:
                    with owner.lock:
                        owner.counts[self.key]["tls_failures"] += 1
                    connection.close()
                    raise

            def handle_error(self, request, client_address):
                pass

        class Admin(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                with owner.lock:
                    body = json.dumps(
                        {"phase": phase, "targets": owner.counts}
                    ).encode()
                self.send_response(200 if self.path in ("/ready", "/state") else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        try:
            for scrape in SCRAPES:
                for case in CASES:
                    key = scrape + "/" + case
                    self.counts[key] = dict.fromkeys(
                        (
                            "attempts",
                            "tls_failures",
                            "http_requests",
                            "authenticated_requests",
                        ),
                        0,
                    )
                    port = target_port(scrape, case) if port_base else 0
                    server = TLSServer((bind, port), Handler)
                    server.key, server.scrape = key, scrape
                    server.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    server.context.load_cert_chain(
                        self.root / (case + ".crt"), self.root / (case + ".key")
                    )
                    self.servers[key] = server
            self.admin = http.server.ThreadingHTTPServer(
                ("127.0.0.1", admin_port), Admin
            )
        except Exception:
            for server in self.servers.values():
                server.server_close()
            raise

    def start(self):
        for server in [*self.servers.values(), self.admin]:
            thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
            )
            thread.start()
            self.threads.append(thread)

    def close(self):
        for server in [*self.servers.values(), self.admin]:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("material", "serve"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--phase", choices=PHASES, default="secure")
    args = parser.parse_args()
    if args.action == "material":
        create_material(args.directory)
        return
    witness = Witness(args.directory, args.phase)
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    try:
        witness.start()
        stopped.wait()
    finally:
        witness.close()


if __name__ == "__main__":
    main()
