"""Focused fixture controls; synthetic debug records are not Alloy runtime proof."""

import ast
import base64
import copy
import http.client
import json
import pathlib
import shlex
import ssl
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tls_fixture as fixture


def component(scrape, phase, secure=True):
    targets = []
    for case in fixture.CASES:
        error = ""
        if secure and case == "wrong-ca":
            error = "tls: failed to verify certificate: x509: certificate signed by unknown authority"
        if secure and case == "wrong-name":
            error = "tls: failed to verify certificate: x509: certificate is valid for wrong.invalid, not kubernetes.default.svc"
        values = {
            "url": fixture.target_url(scrape, case),
            "health": "down" if error else "up",
            "last_error": error,
            "last_scrape": "2026-10-30T17:00:01Z",
        }
        targets.append(
            {
                "name": "target",
                "type": "block",
                "body": [
                    {"name": k, "type": "attr", "value": {"type": "string", "value": v}}
                    for k, v in values.items()
                ],
            }
        )
    return {
        "name": "prometheus.scrape",
        "localID": "prometheus.scrape." + scrape,
        "debugInfo": targets,
    }


def evidence(secure=True):
    phase = "secure" if secure else "mutation"
    witness = {"phase": phase, "targets": {}}
    samples = []
    components = {}
    for scrape in fixture.SCRAPES:
        components[scrape] = component(scrape, phase, secure)
        for case in fixture.CASES:
            accepted = not secure or case == "trusted"
            witness["targets"][scrape + "/" + case] = {
                "attempts": 1,
                "tls_failures": 0 if accepted else 1,
                "http_requests": 1 if accepted else 0,
                "authenticated_requests": 1 if accepted else 0,
            }
            if accepted:
                for name in fixture.METRICS[scrape]:
                    samples.append(
                        {
                            "__name__": name,
                            "tls_scrape": scrape,
                            "tls_case": case,
                            "tls_phase": phase,
                            "cluster": "run",
                        }
                    )
    return phase, witness, components, samples


class TLSProofEvidence(unittest.TestCase):
    def validate(self, data, insecure=False):
        phase, witness, components, samples = data
        fixture.validate_evidence(
            phase, "run", 1793379600, witness, components, samples, insecure=insecure
        )

    def test_known_positive_and_tls_denials(self):
        self.validate(evidence())

    def test_no_attempt_is_not_denial(self):
        data = evidence()
        data[1]["targets"]["cadvisor/wrong-ca"]["attempts"] = 0
        with self.assertRaises(AssertionError):
            self.validate(data)

    def test_timeout_is_not_certificate_failure(self):
        data = evidence()
        attrs = data[2]["cadvisor"]["debugInfo"][1]["body"]
        next(a for a in attrs if a["name"] == "last_error")["value"][
            "value"
        ] = "context deadline exceeded"
        with self.assertRaises(AssertionError):
            self.validate(data)

    def test_missing_or_duplicate_target_is_rejected(self):
        for change in [lambda x: x.pop(), lambda x: x.append(copy.deepcopy(x[0]))]:
            data = evidence()
            change(data[2]["kubelet"]["debugInfo"])
            with self.assertRaises(AssertionError):
                self.validate(data)

    def test_old_scrape_is_rejected(self):
        data = evidence()
        attrs = data[2]["cadvisor"]["debugInfo"][1]["body"]
        next(a for a in attrs if a["name"] == "last_scrape")["value"][
            "value"
        ] = "2026-10-29T17:00:01Z"
        with self.assertRaises(AssertionError):
            self.validate(data)

    def test_old_phase_counter_or_metric_is_rejected(self):
        data = evidence()
        data[1]["phase"] = "mutation"
        with self.assertRaises(AssertionError):
            self.validate(data)
        data = evidence()
        data[3][0]["tls_phase"] = "mutation"
        with self.assertRaises(AssertionError):
            self.validate(data)

    def test_positive_requires_every_metric_and_http(self):
        data = evidence()
        data[3].pop()
        with self.assertRaises(AssertionError):
            self.validate(data)
        data = evidence()
        data[1]["targets"]["kubelet/trusted"]["authenticated_requests"] = 0
        with self.assertRaises(AssertionError):
            self.validate(data)

    def test_authenticated_http_breaks_denial(self):
        data = evidence()
        data[1]["targets"]["cadvisor/wrong-ca"]["authenticated_requests"] = 1
        with self.assertRaises(AssertionError):
            self.validate(data)

    def test_internal_up_zero_does_not_count_as_endpoint_sample(self):
        data = evidence()
        data[3].append(
            {
                "__name__": "up",
                "tls_scrape": "cadvisor",
                "tls_case": "wrong-ca",
                "tls_phase": "secure",
                "cluster": "run",
                "__value__": "0",
            }
        )
        self.validate(data)

    def test_successful_endpoint_sample_breaks_denial(self):
        data = evidence()
        sample = dict(data[3][0], tls_case="wrong-name")
        data[3].append(sample)
        with self.assertRaises(AssertionError):
            self.validate(data)

    def test_insecure_mutation_fails_same_denial_validator(self):
        data = evidence(secure=False)
        self.validate(data, insecure=True)
        with self.assertRaises(AssertionError):
            self.validate(data)

    def test_malformed_typed_value_is_rejected(self):
        data = evidence()
        data[2]["cadvisor"]["debugInfo"][0]["body"][0]["value"] = {
            "type": "number",
            "value": 42,
        }
        with self.assertRaises(AssertionError):
            self.validate(data)

    def test_exact_binary_version(self):
        good = "alloy, version v1.21.2 (branch: HEAD, revision: abc123)\n  platform: linux/amd64"
        self.assertEqual(fixture.check_version(good, "v1.21.2"), "v1.21.2")
        for version in ["v1.18.1", "v1.21.20", "v1.21.2-rc.1", "v1.21.2+other"]:
            with self.assertRaises(AssertionError):
                fixture.check_version(good.replace("v1.21.2", version), "v1.21.2")


class NativeTLSWitness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls.temporary.name) / "material"
        fixture.create_material(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.witness = fixture.Witness(
            self.root, "secure", bind="127.0.0.1", port_base=0, admin_port=0
        )
        self.witness.start()

    def tearDown(self):
        self.witness.close()

    def state(self):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.witness.admin.server_port, timeout=5
        )
        connection.request("GET", "/state")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        data = json.loads(response.read())
        connection.close()
        return data

    def request(self, scrape, case, insecure=False):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=str(self.root / "ca.crt"))
        if insecure:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        connection = http.client.HTTPSConnection(
            "127.0.0.1",
            self.witness.servers[scrape + "/" + case].server_port,
            context=context,
            timeout=5,
        )
        # Connect to loopback while verifying the same SNI used by Alloy.
        import socket

        raw = socket.create_connection((connection.host, connection.port), timeout=5)
        try:
            connection.sock = context.wrap_socket(
                raw, server_hostname="kubernetes.default.svc"
            )
            token = (self.root / "token").read_text()
            connection.request(
                "GET",
                fixture.target_path(scrape),
                headers={"Authorization": "Bearer " + token},
            )
            response = connection.getresponse()
            body = response.read().decode()
            self.assertEqual(response.status, 200)
            self.assertIn(fixture.METRICS[scrape][0], body)
        finally:
            raw.close()
            connection.close()

    def test_admin_readiness_does_not_count_as_tls_attempt(self):
        for _ in range(3):
            state = self.state()
        self.assertTrue(all(row["attempts"] == 0 for row in state["targets"].values()))

    def test_real_tls_positive_negative_and_insecure_controls(self):
        for scrape in fixture.SCRAPES:
            self.request(scrape, "trusted")
            for case in ("wrong-ca", "wrong-name"):
                with self.assertRaises(ssl.SSLCertVerificationError):
                    self.request(scrape, case)
        state = self.state()
        for scrape in fixture.SCRAPES:
            self.assertEqual(
                state["targets"][scrape + "/trusted"]["authenticated_requests"], 1
            )
            for case in ("wrong-ca", "wrong-name"):
                row = state["targets"][scrape + "/" + case]
                self.assertEqual(row["attempts"], 1)
                self.assertEqual(row["tls_failures"], 1)
                self.assertEqual(row["http_requests"], 0)
                self.request(scrape, case, insecure=True)
        state = self.state()
        for scrape in fixture.SCRAPES:
            for case in fixture.CASES:
                self.assertEqual(
                    state["targets"][scrape + "/" + case]["authenticated_requests"], 1
                )


class NativePhaseDeadline(unittest.TestCase):
    """Execute the actual driver phase with a controlled clock, without guests."""

    def run_phase(self, late=False):
        class Clock:
            now = 0.0

            def monotonic(self):
                return self.now

            def sleep(self, seconds):
                self.now += seconds

        clock = Clock()
        commands = []

        class Server:
            def succeed(self, command, timeout=None):
                commands.append((command, timeout))
                if command == "date +%s.%N":
                    return "1000"
                if " get pod " in command:
                    return json.dumps(
                        {
                            "status": {
                                "containerStatuses": [{"imageID": "fixture@sha256:d"}],
                                "podIP": "10.42.0.10",
                            }
                        }
                    )
                if " -- /bin/alloy --version" in command:
                    return "alloy, version v1.21.2 (branch: HEAD, revision: abc123)"
                return ""

            def wait_until_succeeds(self, command, timeout=None):
                if command == "observe":
                    return self.execute(command, timeout)[1]
                return ""

            def execute(self, command, timeout=None):
                commands.append((command, timeout))
                if late:
                    clock.now += 181
                return 0, "{}"

        source = pathlib.Path(__file__).with_name("test_tls_script.py").read_text()
        tree = ast.parse(source)
        names = {
            "require",
            "write_guest",
            "phase_remaining",
            "phase_succeed",
            "phase_observe",
        }
        functions = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        phase = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and node.target.id == "phase"
        )
        namespace = {
            "server": Server(),
            "time": clock,
            "json": json,
            "base64": base64,
            "shlex": shlex,
            "REPLICA": "/replica",
            "PAYLOAD": "/payload",
            "RUN_ID": "run",
            "payload": {"image": "fixture@sha256:d"},
            "pins": {"image_version": "v1.21.2"},
            "results": [],
            "versions": [],
            "image_ids": [],
            "observe_command": lambda *_: "observe",
        }
        exec(
            compile(
                ast.Module(body=functions + [phase], type_ignores=[]),
                "actual-driver-phase",
                "exec",
            ),
            namespace,
        )
        return commands

    def test_late_native_observation_cannot_pass_phase(self):
        with self.assertRaisesRegex(AssertionError, "TLS phase deadline"):
            self.run_phase(late=True)

    def test_every_phase_command_has_remaining_timeout(self):
        commands = self.run_phase()
        for command, timeout in commands:
            if command.startswith("systemd-run"):
                continue
            self.assertIsNotNone(timeout, command)
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 180)


if __name__ == "__main__":
    unittest.main()
