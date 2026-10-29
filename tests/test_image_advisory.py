"""Offline contracts. Synthetic process replies do not prove native detection."""

import copy
import contextlib
import hashlib
import importlib.util
import json
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("image_advisory", ROOT / "scripts/scan-image-advisory.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AdvisoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.sbom = self.root / "source.json"
        self.trivy = self.root / "trivy"
        self.trivy.write_bytes(b"synthetic executable identity")
        self.cache = self.root / "cache"
        (self.cache / "db").mkdir(parents=True)
        (self.cache / "db/trivy.db").write_bytes(b"synthetic DB bytes")
        now = datetime.now(timezone.utc)
        self.metadata = {"Version": 2, "UpdatedAt": (now - timedelta(hours=1)).isoformat(),
                         "NextUpdate": (now + timedelta(hours=1)).isoformat(),
                         "DownloadedAt": now.isoformat()}
        self.write_metadata()
        self.output = self.root / "evidence"
        self.bom = {"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
                    "components": [{"type": "library", "name": "lodash", "version": "4.17.11",
                                    "purl": "pkg:npm/lodash@4.17.11"}]}
        self.write_sbom()
        self.native = {"SchemaVersion": 2, "Trivy": {"Version": "0.70.0"},
                       "ArtifactType": "cyclonedx", "Results": [
                           {"Target": "opaque local path", "Class": "lang-pkgs", "Type": "npm",
                            "Packages": [{"Name": "lodash", "Version": "4.17.11"}],
                            "Vulnerabilities": [{"VulnerabilityID": "CVE-2019-10744", "PkgName": "lodash",
                                                 "InstalledVersion": "4.17.11", "Severity": "CRITICAL",
                                                 "Title": "SYNTHETIC_PRIVATE_CANARY"}]}]}
        self.commands = []

    def write_sbom(self):
        self.sbom.write_text(json.dumps(self.bom))

    def write_metadata(self):
        (self.cache / "db/metadata.json").write_text(json.dumps(self.metadata))

    def process(self, command, **kwargs):
        self.commands.append((command, kwargs))
        if "--version" in command:
            kwargs["stdout"].write(b"Version: 0.70.0\n")
        else:
            Path(command[command.index("--output") + 1]).write_text(json.dumps(self.native))
        return subprocess.CompletedProcess(command, 0)

    def run_scan(self, process=None):
        with patch.object(MODULE.subprocess, "run", side_effect=process or self.process):
            return MODULE.scan(self.sbom, self.trivy, self.cache, self.output)

    def test_critical_findings_are_advisory_with_immutable_evidence(self):
        result = self.run_scan()
        self.assertIsInstance(result, dict)
        self.assertEqual(result["mode"], "advisory")
        self.assertEqual(result["findings"], 1)
        self.assertEqual(result["subject"], {"kind": "sbom", "sha256": hashlib.sha256(self.sbom.read_bytes()).hexdigest(),
                                           "image_association": "unverified"})
        self.assertEqual(result["scanner"]["version"], "0.70.0")
        self.assertEqual(result["scanner"]["sha256"], hashlib.sha256(self.trivy.read_bytes()).hexdigest())
        self.assertEqual(result["database"]["sha256"], hashlib.sha256(b"synthetic DB bytes").hexdigest())
        self.assertEqual(result["database"]["metadata"], self.metadata)
        self.assertEqual((self.output / "sbom.cdx.json").read_bytes(), self.sbom.read_bytes())
        self.assertEqual(result["report_sha256"], hashlib.sha256((self.output / "trivy.json").read_bytes()).hexdigest())
        self.assertNotIn("SYNTHETIC_PRIVATE_CANARY", json.dumps(result))
        self.assertEqual(json.loads((self.output / "receipt.json").read_text()), result)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(p.stat().st_mode & 0o077 == 0 for p in self.output.iterdir() if p.is_file()))

    def test_no_findings_requires_nonempty_complete_package_inventory(self):
        del self.native["Results"][0]["Vulnerabilities"]
        result = self.run_scan()
        self.assertIsInstance(result, dict)
        self.assertEqual(result["findings"], 0)
        self.assertEqual(result["packages"], 1)
        self.assertEqual(result["security_verdict"], "not-established")

    def test_native_sbom_node_package_class_is_supported(self):
        self.native["Results"][0]["Type"] = "node-pkg"
        result = None
        try:
            result = self.run_scan()
        except ValueError:
            pass
        self.assertIsInstance(result, dict)
        self.assertEqual(result["findings"], 1)

    def test_scanner_command_is_explicit_local_offline_and_unfiltered(self):
        with patch.dict(os.environ, {"TRIVY_SERVER": "https://invalid.example", "TOKEN": "CANARY"}):
            self.run_scan()
        self.assertEqual(len(self.commands), 2)
        command, settings = self.commands[-1]
        self.assertIn("sbom", command)
        for flag in ("--offline-scan", "--skip-db-update", "--skip-java-db-update", "--list-all-pkgs"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--exit-code") + 1], "0")
        self.assertEqual(command[command.index("--scanners") + 1], "vuln")
        self.assertEqual(command[-1], str(self.output / "sbom.cdx.json"))
        self.assertNotIn("TRIVY_SERVER", settings["env"])
        self.assertNotIn("TOKEN", settings["env"])
        self.assertEqual(Path(settings["cwd"]), self.output)
        self.assertLessEqual(settings["timeout"], 60)

    def test_scanner_failure_never_writes_a_success_receipt(self):
        def failed(command, **kwargs):
            if "--version" in command:
                return self.process(command, **kwargs)
            kwargs["stderr"].write(b"SYNTHETIC_PRIVATE_CANARY")
            return subprocess.CompletedProcess(command, 7)
        with self.assertRaises(ValueError):
            self.run_scan(failed)
        self.assertFalse((self.output / "receipt.json").exists())

    def test_missing_output_and_native_empty_unsupported_or_partial_evidence_fail(self):
        original = copy.deepcopy(self.native)
        for change in (lambda d: d.update(SchemaVersion=3), lambda d: d.update(Results=[]),
                       lambda d: d.update(ArtifactType="container_image"),
                       lambda d: d["Results"][0].update(Type="pip"),
                       lambda d: d["Results"][0].update(Packages=[]),
                       lambda d: d["Results"][0]["Packages"][0].update(Version="other"),
                       lambda d: d["Results"][0]["Vulnerabilities"][0].update(Severity="invalid")):
            with self.subTest(change=change):
                self.native = copy.deepcopy(original)
                change(self.native)
                self.output = self.root / ("evidence-" + str(id(change)))
                with self.assertRaises(ValueError):
                    self.run_scan()
        self.output = self.root / "missing-output"
        def missing(command, **kwargs):
            if "--version" in command:
                return self.process(command, **kwargs)
            return subprocess.CompletedProcess(command, 0)
        with self.assertRaises((ValueError, OSError)):
            self.run_scan(missing)

    def test_bad_sbom_or_unknown_ecosystem_never_launches_a_scan(self):
        for content in ("{}", "", '{"bomFormat":"CycloneDX","bomFormat":"other"}',
                        json.dumps(dict(self.bom, components=[])),
                        json.dumps(dict(self.bom, specVersion="9")),
                        json.dumps(dict(self.bom, components=[dict(self.bom["components"][0], purl="pkg:pypi/test@1")]))):
            with self.subTest(content=content):
                self.sbom.write_text(content)
                with self.assertRaises(ValueError):
                    self.run_scan()
        self.assertEqual(self.commands, [])

    def test_stale_unknown_or_missing_database_is_an_error(self):
        original = self.metadata.copy()
        for change in ({"Version": 3}, {"NextUpdate": "2000-01-01T00:00:00Z"}, {"UpdatedAt": "invalid"}):
            self.metadata = original.copy()
            self.metadata.update(change)
            self.write_metadata()
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.run_scan()
        self.assertEqual(self.commands, [])

    def test_binary_database_or_metadata_mutation_invalidates_receipt(self):
        for target in (self.trivy, self.cache / "db/trivy.db", self.cache / "db/metadata.json"):
            with self.subTest(target=target.name):
                before = target.read_bytes()
                self.output = self.root / ("mutation-" + target.name)
                def changed(command, **kwargs):
                    result = self.process(command, **kwargs)
                    if "sbom" in command:
                        target.write_bytes(b"changed")
                    return result
                with self.assertRaises(ValueError):
                    self.run_scan(changed)
                target.write_bytes(before)

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / "receipt.json"
        sentinel.write_text("prior evidence")
        with self.assertRaises(FileExistsError):
            self.run_scan()
        self.assertEqual(sentinel.read_text(), "prior evidence")

    def test_empty_native_version_is_a_controlled_error(self):
        def empty(command, **kwargs):
            return subprocess.CompletedProcess(command, 0)
        error = None
        try:
            self.run_scan(empty)
        except (ValueError, IndexError) as caught:
            error = caught
        self.assertIsInstance(error, ValueError)

    def test_scanned_snapshot_mutation_invalidates_evidence(self):
        def changed(command, **kwargs):
            result = self.process(command, **kwargs)
            if "sbom" in command:
                target = Path(command[-1])
                target.write_bytes(target.read_bytes() + b"\n")
            return result
        with self.assertRaises(ValueError):
            self.run_scan(changed)
        self.assertFalse((self.output / "receipt.json").exists())

    def test_cli_requires_all_local_inputs_and_failures_are_nonzero(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as missing:
            MODULE.main([])
        self.assertNotEqual(missing.exception.code, 0)
        argv = ["--sbom", str(self.sbom), "--trivy", str(self.trivy),
                "--cache-dir", str(self.cache), "--output-dir", str(self.output)]
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), patch.object(MODULE, "scan", side_effect=ValueError("SYNTHETIC_PRIVATE_CANARY")):
            self.assertNotEqual(MODULE.main(argv), 0)
        self.assertNotIn("SYNTHETIC_PRIVATE_CANARY", stderr.getvalue())
        with contextlib.redirect_stdout(io.StringIO()), patch.object(MODULE, "scan", return_value={"findings": 9}):
            self.assertEqual(MODULE.main(argv), 0)


if __name__ == "__main__":
    unittest.main()
