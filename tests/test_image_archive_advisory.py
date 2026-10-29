"""Offline archive contracts; native detection is verified separately on tiny fixtures."""

import contextlib
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("archive_advisory", ROOT / "scripts/scan-image-advisory.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
IMAGE_ID = "sha256:" + "ab" * 32


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.archive = self.root / "source.tar"
        self.archive.write_bytes(b"explicit synthetic archive input")
        self.expected = self.root / "expected.cdx.json"
        self.bom = json.loads((ROOT / "tests/fixtures/image-advisory/vulnerable.cdx.json").read_text())
        self.expected.write_text(json.dumps(self.bom))
        self.trivy = self.root / "trivy"
        self.trivy.write_bytes(b"synthetic executable identity")
        self.cache = self.root / "cache"
        (self.cache / "db").mkdir(parents=True)
        (self.cache / "db/trivy.db").write_bytes(b"synthetic database")
        now = datetime.now(timezone.utc)
        metadata = {"Version": 2, "UpdatedAt": (now - timedelta(hours=1)).isoformat(),
                    "NextUpdate": (now + timedelta(hours=1)).isoformat(), "DownloadedAt": now.isoformat()}
        (self.cache / "db/metadata.json").write_text(json.dumps(metadata))
        self.output = self.root / "evidence"
        self.native = {"SchemaVersion": 2, "Trivy": {"Version": "0.70.0"}, "ArtifactType": "container_image",
                       "ArtifactID": "sha256:" + "cd" * 32, "Metadata": {"ImageID": IMAGE_ID},
                       "Results": [{"Class": "lang-pkgs", "Type": "node-pkg",
                                    "Packages": [{"Name": "lodash", "Version": "4.17.11"}],
                                    "Vulnerabilities": [{"VulnerabilityID": "CVE-2019-10744", "PkgName": "lodash",
                                                         "InstalledVersion": "4.17.11", "Severity": "CRITICAL",
                                                         "Title": "SYNTHETIC_PRIVATE_CANARY"}]}]}
        self.generated = copy.deepcopy(self.bom)
        self.generated["specVersion"] = "1.6"
        self.generated["metadata"] = {
            "tools": {"components": [{"type": "application", "name": "trivy", "version": "0.70.0"}]},
            "component": {"type": "container", "properties": [
                {"name": "aquasecurity:trivy:ImageID", "value": IMAGE_ID}]}}
        self.commands = []

    def process(self, command, **kwargs):
        self.commands.append((command, kwargs))
        if "--version" in command:
            kwargs["stdout"].write(b"Version: 0.70.0\n")
        else:
            report = copy.deepcopy(self.native if command[command.index("--format") + 1] == "json" else self.generated)
            image_path = command[command.index("--input") + 1]
            if report.get("ArtifactType") == "container_image":
                report.setdefault("ArtifactName", image_path)
            elif "metadata" in report:
                report["metadata"].get("component", {}).setdefault("name", image_path)
            Path(command[command.index("--output") + 1]).write_text(json.dumps(report))
        return subprocess.CompletedProcess(command, 0)

    def scan(self, process=None):
        with patch.object(MODULE.subprocess, "run", side_effect=process or self.process):
            return MODULE.scan_archive(self.archive, self.expected, self.trivy, self.cache, self.output)

    def test_vulnerabilities_are_advisory_and_identities_remain_distinct(self):
        receipt = self.scan()
        self.assertIsInstance(receipt, dict)
        self.assertEqual(receipt["mode"], "advisory")
        self.assertEqual(receipt["findings"], 1)
        self.assertEqual(receipt["security_verdict"], "not-established")
        self.assertEqual(receipt["subject"], {
            "kind": "image-archive", "sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest(),
            "config_digest": IMAGE_ID, "config_digest_basis": "scanner-reported",
            "registry_manifest_digest": None, "deployed_image_association": "unverified"})
        self.assertEqual(receipt["sbom_association"], "generated-from-same-archive")
        self.assertEqual(receipt["coverage"], {"ecosystems": ["npm"], "whole_image": "not-established"})
        self.assertEqual((self.output / "image.tar").read_bytes(), self.archive.read_bytes())
        self.assertEqual((self.output / "expected-sbom.cdx.json").read_bytes(), self.expected.read_bytes())
        self.assertEqual(receipt["generated_sbom_sha256"], hashlib.sha256((self.output / "generated-sbom.cdx.json").read_bytes()).hexdigest())
        self.assertEqual(receipt["report_sha256"], hashlib.sha256((self.output / "trivy.json").read_bytes()).hexdigest())
        self.assertEqual(json.loads((self.output / "receipt.json").read_text()), receipt)
        self.assertNotIn("SYNTHETIC_PRIVATE_CANARY", json.dumps(receipt))
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(p.stat().st_mode & 0o077 == 0 for p in self.output.iterdir() if p.is_file()))

    def test_zero_findings_still_require_inventory_and_no_security_verdict(self):
        self.native["Results"][0]["Vulnerabilities"] = []
        receipt = self.scan()
        self.assertIsInstance(receipt, dict)
        self.assertEqual((receipt["packages"], receipt["findings"]), (1, 0))
        self.assertEqual(receipt["security_verdict"], "not-established")

    def test_both_native_outputs_use_only_the_same_local_snapshot(self):
        receipt = self.scan()
        self.assertIsInstance(receipt, dict)
        scans = [(command, kw) for command, kw in self.commands if "image" in command]
        self.assertEqual(len(scans), 2)
        self.assertEqual([c[c.index("--format") + 1] for c, _ in scans], ["json", "cyclonedx"])
        for command, kw in scans:
            self.assertEqual(command[command.index("--input") + 1], str(self.output / "image.tar"))
            self.assertNotIn(str(self.archive), command)
            self.assertEqual(set(kw["env"]), {"HOME", "PATH"})
            self.assertEqual(kw["timeout"], 60)
            for option in ["--offline-scan", "--skip-db-update", "--skip-java-db-update", "--skip-version-check", "--disable-telemetry"]:
                self.assertIn(option, command)
            self.assertEqual(command[command.index("--exit-code") + 1], "0")

    def test_native_failure_at_either_stage_cannot_write_success(self):
        for number, (fmt, timeout) in enumerate([("json", False), ("cyclonedx", False),
                                               ("json", True), ("cyclonedx", True)]):
            self.output = self.root / ("failure-" + str(number))
            def process(command, **kwargs):
                if "--format" in command and command[command.index("--format") + 1] == fmt:
                    if timeout:
                        raise subprocess.TimeoutExpired(command, 60, stderr="SYNTHETIC_PRIVATE_CANARY")
                    return subprocess.CompletedProcess(command, 7)
                return self.process(command, **kwargs)
            args = ["--image-archive", str(self.archive), "--expected-sbom", str(self.expected),
                    "--trivy", str(self.trivy), "--cache-dir", str(self.cache), "--output-dir", str(self.output)]
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.subTest(stage=fmt, timeout=timeout), patch.object(MODULE.subprocess, "run", side_effect=process), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(MODULE.main(args), 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertNotIn("SYNTHETIC_PRIVATE_CANARY", stderr.getvalue())
            self.assertFalse((self.output / "receipt.json").exists())

    def test_missing_extra_or_unsupported_inventory_fails(self):
        cases = [
            lambda: self.native["Results"][0].update(Packages=[]),
            lambda: self.native["Results"][0]["Packages"].append({"Name": "extra", "Version": "1"}),
            lambda: self.native["Results"][0].update(Class="os-pkgs", Type="alpine"),
            lambda: self.native["Metadata"].update(OS={"Family": "alpine", "Name": "3.20"}),
            lambda: self.generated.update(components=[]),
            lambda: self.generated["components"][0].update(version="4.17.21", purl="pkg:npm/lodash@4.17.21"),
            lambda: self.generated["components"][0].update(purl="pkg:pypi/lodash@4.17.11"),
        ]
        native, generated = copy.deepcopy(self.native), copy.deepcopy(self.generated)
        for number, mutate in enumerate(cases):
            self.native, self.generated = copy.deepcopy(native), copy.deepcopy(generated)
            self.output = self.root / ("coverage-" + str(number))
            mutate()
            with self.subTest(case=number), self.assertRaises(ValueError):
                self.scan()
            self.assertFalse((self.output / "receipt.json").exists())

    def test_native_sbom_identity_version_and_subject_must_agree(self):
        cases = [
            lambda: self.native["Metadata"].update(ImageID="not-a-digest"),
            lambda: self.native.update(ArtifactName="another-input.tar"),
            lambda: self.generated["metadata"]["component"].update(type="application"),
            lambda: self.generated["metadata"]["component"].update(name="another-input.tar"),
            lambda: self.generated["metadata"]["component"]["properties"][0].update(value="sha256:" + "ef" * 32),
            lambda: self.generated["metadata"]["component"]["properties"].append({"name": "aquasecurity:trivy:ImageID", "value": IMAGE_ID}),
            lambda: self.generated["metadata"]["tools"]["components"][0].update(version="0.69.0"),
        ]
        native, generated = copy.deepcopy(self.native), copy.deepcopy(self.generated)
        for number, mutate in enumerate(cases):
            self.native, self.generated = copy.deepcopy(native), copy.deepcopy(generated)
            self.output = self.root / ("identity-" + str(number))
            mutate()
            with self.subTest(case=number), self.assertRaises(ValueError):
                self.scan()
            self.assertFalse((self.output / "receipt.json").exists())

    def test_mutation_of_input_database_snapshot_or_prior_output_fails(self):
        paths = [self.archive, self.expected, self.trivy, self.cache / "db/trivy.db", self.cache / "db/metadata.json"]
        originals = {path: path.read_bytes() for path in paths}
        names = [*paths, "image.tar", "expected-sbom.cdx.json", "trivy.yaml", "ignore", "trivy.json"]
        for number, target in enumerate(names):
            for path, raw in originals.items():
                path.write_bytes(raw)
            self.output = self.root / ("mutation-" + str(number))
            def process(command, **kwargs):
                result = self.process(command, **kwargs)
                if "cyclonedx" in command:
                    path = target if isinstance(target, Path) else self.output / target
                    path.write_bytes(b"changed during collection")
                return result
            with self.subTest(target=str(target)), self.assertRaises(ValueError):
                self.scan(process)
            self.assertFalse((self.output / "receipt.json").exists())

    def test_missing_empty_oversized_archive_rejected_before_process(self):
        for kind in ["missing", "empty", "oversized"]:
            self.archive.unlink(missing_ok=True)
            if kind != "missing":
                with self.archive.open("wb") as stream:
                    if kind == "oversized":
                        stream.truncate(64 * 1024 * 1024 + 1)
            self.output = self.root / ("invalid-" + kind)
            with self.subTest(kind=kind), patch.object(MODULE.subprocess, "run") as process:
                with self.assertRaises((ValueError, OSError)):
                    MODULE.scan_archive(self.archive, self.expected, self.trivy, self.cache, self.output)
                process.assert_not_called()

    def test_existing_output_is_never_replaced(self):
        self.output.mkdir()
        sentinel = self.output / "receipt.json"
        sentinel.write_bytes(b"prior evidence")
        with self.assertRaises((ValueError, OSError)):
            self.scan()
        self.assertEqual(sentinel.read_bytes(), b"prior evidence")

    def test_malformed_generated_sbom_is_not_success(self):
        def process(command, **kwargs):
            result = self.process(command, **kwargs)
            if "cyclonedx" in command:
                Path(command[command.index("--output") + 1]).write_text('{"duplicate":1,"duplicate":2}')
            return result
        with self.assertRaises(ValueError):
            self.scan(process)
        self.assertFalse((self.output / "receipt.json").exists())

    def test_cli_archive_mode_is_opt_in_and_reports_only_aggregate_status(self):
        args = ["--image-archive", str(self.archive), "--expected-sbom", str(self.expected),
                "--trivy", str(self.trivy), "--cache-dir", str(self.cache), "--output-dir", str(self.output)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(MODULE.subprocess, "run", side_effect=self.process), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                status = MODULE.main(args)
            except SystemExit as error:
                status = error.code
        self.assertEqual(status, 0, stderr.getvalue())
        self.assertIn("IMAGE_ARCHIVE_ADVISORY_OK", stdout.getvalue())
        self.assertNotIn("SYNTHETIC_PRIVATE_CANARY", stdout.getvalue() + stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
