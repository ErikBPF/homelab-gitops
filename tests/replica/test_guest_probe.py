"""Native loopback controls for the replica's API evidence helper."""

import base64
import http.server
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import unittest


PROBE = Path(__file__).with_name("guest_probe.py")
TOKEN = "replica-only-token-must-not-appear-in-output"
CANARY = base64.b64encode(b"replica synthetic canary").decode()


class ProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="alloy-probe-test-")
        cls.root = Path(cls.temp.name)
        cls.cert = cls.root / "ca.crt"
        cls.key = cls.root / "server.key"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(cls.key), "-out", str(cls.cert), "-days", "1",
             "-subj", "/CN=replica", "-addext", "subjectAltName=IP:127.0.0.1"],
            check=True, capture_output=True,
        )
        cls.state = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                cls.state["requests"] += 1
                if self.headers.get("Authorization") != "Bearer " + TOKEN:
                    self.send_response(401)
                    self.end_headers()
                    return
                self.send_response(cls.state["status"])
                if cls.state["status"] == 302:
                    self.send_header("Location", "https://127.0.0.1:6443/redirected")
                body = cls.state["body"]
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        cls.server = http.server.HTTPServer(("127.0.0.1", 6443), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.cert, cls.key)
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def setUp(self):
        self.state.update(status=200, requests=0, body=json.dumps({"data": {"canary": CANARY}}).encode())
        self.config = {
            "apiVersion": "v1", "kind": "Config", "current-context": "replica",
            "clusters": [{"name": "replica", "cluster": {
                "server": "https://127.0.0.1:6443", "certificate-authority": str(self.cert)}}],
            "users": [{"name": "replica", "user": {"token": TOKEN}}],
            "contexts": [{"name": "replica", "context": {"cluster": "replica", "user": "replica"}}],
        }
        self.path = self.root / "kubeconfig.json"
        self.path.write_text(json.dumps(self.config))
        self.path.chmod(0o600)

    def run_probe(self, status=200, *, pointer="/data/canary", expected=CANARY, contains=None, timeout=15):
        command = [os.sys.executable, str(PROBE), "--kubeconfig", str(self.path),
                   "--path", "/api/v1/namespaces/replica/secrets/canary", "--expect-status", str(status)]
        if contains is not None:
            command += ["--body-contains", contains]
        elif status == 200 and pointer is not None:
            command += ["--json-pointer", pointer, "--expect-value", expected]
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout,
                                env={"PATH": os.environ["PATH"], "HTTPS_PROXY": "http://127.0.0.1:1"})
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        self.assertNotIn(CANARY, result.stdout + result.stderr)
        return result

    def test_known_object_requires_actual_authenticated_read(self):
        result = self.run_probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ALLOY_REPLICA_API_OK", result.stdout)
        self.assertEqual(self.state["requests"], 1)

    def test_unexpected_success_cannot_count_as_denial(self):
        result = self.run_probe(403)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("ALLOY_REPLICA_API_OK", result.stdout)

    def test_real_forbidden_status_is_required(self):
        self.state.update(status=403, body=json.dumps({"kind": "Status", "reason": "Forbidden", "code": 403}).encode())
        self.assertEqual(self.run_probe(403).returncode, 0)

    def test_missing_unauthenticated_redirect_or_error_is_not_denial(self):
        for status in (401, 302, 404, 500):
            with self.subTest(status=status):
                self.state.update(status=status, requests=0)
                self.assertNotEqual(self.run_probe(403).returncode, 0)
                self.assertEqual(self.state["requests"], 1)

    def test_wrong_body_or_missing_positive_control_fails(self):
        self.assertNotEqual(self.run_probe(expected="wrong").returncode, 0)
        self.assertNotEqual(self.run_probe(pointer=None).returncode, 0)
        self.assertNotEqual(self.run_probe(contains="").returncode, 0)

    def test_log_positive_control(self):
        self.state["body"] = b"replica-log-marker\n"
        self.assertEqual(self.run_probe(contains="replica-log-marker").returncode, 0)

    def test_permissive_kubeconfig_rejected_before_request(self):
        self.path.chmod(0o644)
        self.assertNotEqual(self.run_probe().returncode, 0)
        self.assertEqual(self.state["requests"], 0)

    def test_fifo_rejected_promptly_before_request(self):
        self.path.unlink()
        os.mkfifo(self.path, 0o600)
        try:
            try:
                result = self.run_probe(timeout=2)
            except subprocess.TimeoutExpired:
                self.fail("FIFO kubeconfig blocked before regular-file validation")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.state["requests"], 0)
        finally:
            self.path.unlink()

    def test_external_server_or_exec_auth_rejected_before_request(self):
        for mutation in ("server", "exec"):
            with self.subTest(mutation=mutation):
                self.setUp()
                if mutation == "server":
                    self.config["clusters"][0]["cluster"]["server"] = "https://example.invalid:6443"
                else:
                    self.config["users"][0]["user"]["exec"] = {"command": "must-not-run"}
                self.path.write_text(json.dumps(self.config))
                self.assertNotEqual(self.run_probe().returncode, 0)
                self.assertEqual(self.state["requests"], 0)

    def test_wrong_ca_rejected_before_http_authentication(self):
        other = self.root / "unrelated.crt"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(self.root / "unrelated.key"), "-out", str(other),
                        "-days", "1", "-subj", "/CN=unrelated"], check=True, capture_output=True)
        self.config["clusters"][0]["cluster"]["certificate-authority"] = str(other)
        self.path.write_text(json.dumps(self.config))
        self.assertNotEqual(self.run_probe().returncode, 0)
        self.assertEqual(self.state["requests"], 0)

    def test_reflected_credential_never_printed(self):
        self.state.update(status=403, body=json.dumps({"kind": "Status", "reason": TOKEN, "code": 403}).encode())
        self.assertNotEqual(self.run_probe(403).returncode, 0)


if __name__ == "__main__":
    unittest.main()
