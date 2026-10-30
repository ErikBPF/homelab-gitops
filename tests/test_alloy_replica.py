"""Exercise actual Helm payload preparation and preboot refusal controls."""

import copy
import pathlib
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests/replica"))
from check_payload import check_payload
from prepare_payload import prepare


class ReplicaPayload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.run_id = "e92a8411-e02c-4e9b-a0db-948aee85b8f6"
        cls.source = (ROOT / "platform/alloy-metrics/values.yaml").read_bytes()
        cls.config = yaml.safe_load(cls.source)["alloy"]["alloy"]["configMap"][
            "content"
        ]
        cls.documents = cls.render(cls.run_id)

    @classmethod
    def render(cls, run_id):
        with tempfile.TemporaryDirectory() as directory:
            overlay = pathlib.Path(directory) / "values.yaml"
            overlay.write_text(yaml.safe_dump(prepare(cls.source, run_id)))
            result = subprocess.check_output(
                [
                    "helm",
                    "template",
                    "alloy-metrics",
                    str(ROOT / "platform/alloy-metrics"),
                    "--namespace",
                    "monitoring",
                    "--values",
                    str(overlay),
                ],
                text=True,
            )
        return [item for item in yaml.safe_load_all(result) if item]

    def test_actual_prepared_chart_is_contained(self):
        check_payload(self.documents, self.config, self.run_id)

    def test_actual_fresh_run_ids_are_contained(self):
        for run_id in [
            "732f2527-a59a-4d21-bec6-ce99cc619c84",
            "b92a8411-e02c-4e9b-a0db-948aee85b8f6",
        ]:
            with self.subTest(run_id=run_id):
                check_payload(self.render(run_id), self.config, run_id)

    def test_wrong_configuration_checksum_is_refused(self):
        documents = copy.deepcopy(self.documents)
        deployment = next(item for item in documents if item["kind"] == "Deployment")
        deployment["spec"]["template"]["metadata"]["annotations"]["checksum/config"] = (
            "0" * 63
        )
        with self.assertRaises(AssertionError):
            check_payload(documents, self.config, self.run_id)

    def test_unknown_source_target_is_refused(self):
        changed = self.source.replace(b"127.0.0.1:12345", b"unexpected.invalid:12345")
        with self.assertRaisesRegex(AssertionError, "source changed"):
            prepare(changed, self.run_id)

    def test_injected_rendered_target_is_refused(self):
        documents = copy.deepcopy(self.documents)
        config = next(
            item
            for item in documents
            if item["kind"] == "ConfigMap" and "config.alloy" in item.get("data", {})
        )
        config["data"][
            "config.alloy"
        ] += '\nprometheus.scrape "unexpected" {\n targets = [{ "__address__" = "unexpected.invalid:80" }]\n}\n'
        with self.assertRaisesRegex(AssertionError, "unknown static target"):
            check_payload(documents, self.config, self.run_id)

    def test_actual_scrape_tls_mutation_is_refused(self):
        documents = copy.deepcopy(self.documents)
        config = next(
            item
            for item in documents
            if item["kind"] == "ConfigMap" and "config.alloy" in item.get("data", {})
        )
        config["data"]["config.alloy"] = config["data"]["config.alloy"].replace(
            'server_name = "kubernetes.default.svc"', "insecure_skip_verify = true", 1
        )
        with self.assertRaisesRegex(AssertionError, "cadvisor scrape changed"):
            check_payload(documents, self.config, self.run_id)

    def test_reused_payload_cannot_claim_another_run(self):
        with self.assertRaisesRegex(AssertionError, "run ID absent"):
            check_payload(
                self.documents, self.config, "b92a8411-e02c-4e9b-a0db-948aee85b8f6"
            )

    def test_changed_discovery_expression_is_refused(self):
        documents = copy.deepcopy(self.documents)
        config = next(
            item
            for item in documents
            if item["kind"] == "ConfigMap" and "config.alloy" in item.get("data", {})
        )
        config["data"]["config.alloy"] = config["data"]["config.alloy"].replace(
            'role = "node"', 'role = "pod"'
        )
        with self.assertRaisesRegex(AssertionError, "configuration differs"):
            check_payload(documents, self.config, self.run_id)

    def test_extra_resource_is_refused(self):
        extra = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": "unreviewed", "namespace": "monitoring"},
        }
        with self.assertRaisesRegex(AssertionError, "unexpected resource"):
            check_payload([*self.documents, extra], self.config, self.run_id)

    def test_unreviewed_container_environment_is_refused(self):
        documents = copy.deepcopy(self.documents)
        deployment = next(item for item in documents if item["kind"] == "Deployment")
        deployment["spec"]["template"]["spec"]["containers"][0].setdefault(
            "env", []
        ).append({"name": "HTTP_PROXY", "value": "http://unreviewed.invalid:8080"})
        with self.assertRaisesRegex(
            AssertionError, "complete rendered payload changed"
        ):
            check_payload(documents, self.config, self.run_id)

    def test_optimized_python_still_refuses_unreviewed_image(self):
        documents = copy.deepcopy(self.documents)
        deployment = next(item for item in documents if item["kind"] == "Deployment")
        deployment["spec"]["template"]["spec"]["containers"][0][
            "image"
        ] = "unreviewed.invalid/image:latest"
        with tempfile.TemporaryDirectory() as directory:
            manifest = pathlib.Path(directory) / "payload.yaml"
            manifest.write_text(yaml.safe_dump_all(documents))
            result = subprocess.run(
                [
                    sys.executable,
                    "-O",
                    str(ROOT / "tests/replica/check_payload.py"),
                    str(manifest),
                    str(ROOT / "platform/alloy-metrics/values.yaml"),
                    self.run_id,
                ],
                check=False,
                text=True,
                capture_output=True,
            )
        self.assertNotEqual(
            result.returncode, 0, "optimized Python bypassed the preboot boundary"
        )


if __name__ == "__main__":
    unittest.main()
