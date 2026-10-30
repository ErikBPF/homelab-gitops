"""Debian archive coverage boundaries; native detection has separate fixtures."""

import contextlib
import copy
from datetime import datetime, timedelta, timezone
import io
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_image_archive_advisory as archive

MODULE = archive.MODULE
ROOT = archive.ROOT


class DebianArchiveTests(unittest.TestCase):
    process = archive.ArchiveTests.process
    scan = archive.ArchiveTests.scan

    def setUp(self):
        archive.ArchiveTests.setUp(self)
        self.bom = json.loads((ROOT / "tests/fixtures/image-advisory/debian-vulnerable.cdx.json").read_text())
        self.expected.write_text(json.dumps(self.bom))
        metadata = self.generated["metadata"]
        self.generated = copy.deepcopy(self.bom)
        self.generated["metadata"] = metadata
        self.native["Metadata"]["OS"] = {"Family": "debian", "Name": "12.0"}
        self.native["Metadata"]["ImageConfig"] = {"architecture": "amd64", "os": "linux"}
        self.purl = "pkg:deb/debian/openssl@3.0.9-1?arch=amd64&distro=debian-12.0"
        self.native["Results"] = [{
            "Class": "os-pkgs", "Type": "debian",
            "Packages": [{"Name": "openssl", "Version": "3.0.9", "Release": "1", "Arch": "amd64",
                          "Identifier": {"PURL": self.purl}, "AnalyzedBy": "dpkg"}],
            "Vulnerabilities": [{"VulnerabilityID": "SYNTHETIC-CVE", "PkgName": "openssl",
                                 "InstalledVersion": "3.0.9-1", "PkgIdentifier": {"PURL": self.purl},
                                 "Severity": "CRITICAL"}]}]

    def run_cli(self, process=None, *, standalone=False):
        target = ["--sbom", str(self.expected)] if standalone else [
            "--image-archive", str(self.archive), "--expected-sbom", str(self.expected)]
        args = target + ["--trivy", str(self.trivy), "--cache-dir", str(self.cache),
                         "--output-dir", str(self.output)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(MODULE.subprocess, "run", side_effect=process or self.process), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = MODULE.main(args)
        return status, stdout.getvalue(), stderr.getvalue()

    def success(self):
        status, stdout, stderr = self.run_cli()
        self.assertEqual(status, 0, stderr)
        self.assertIn("IMAGE_ARCHIVE_ADVISORY_OK", stdout)
        return json.loads((self.output / "receipt.json").read_text())

    def test_debian_findings_are_advisory_with_explicit_coverage(self):
        receipt = self.success()
        self.assertEqual((receipt["packages"], receipt["findings"]), (1, 1))
        self.assertEqual(receipt["coverage"], {"ecosystems": ["deb"], "whole_image": "not-established",
                                              "operating_system": {"family": "debian", "version": "12.0"}})
        self.assertEqual(receipt["subject"]["registry_manifest_digest"], None)
        self.assertEqual(receipt["subject"]["deployed_image_association"], "unverified")
        self.assertEqual(receipt["security_verdict"], "not-established")

    def test_zero_findings_require_complete_inventory(self):
        self.bom = json.loads((ROOT / "tests/fixtures/image-advisory/debian-control.cdx.json").read_text())
        self.expected.write_text(json.dumps(self.bom))
        self.generated["components"] = copy.deepcopy(self.bom["components"])
        self.native["Results"][0]["Packages"] = [{
            "Name": "base-files", "Version": "12.4+deb12u12", "Arch": "amd64", "AnalyzedBy": "dpkg",
            "Identifier": {"PURL": "pkg:deb/debian/base-files@12.4%2Bdeb12u12?arch=amd64&distro=debian-12.0"}}]
        self.native["Results"][0]["Vulnerabilities"] = []
        receipt = self.success()
        self.assertEqual((receipt["packages"], receipt["findings"]), (1, 0))

    def test_cli_reports_debian_coverage_without_npm_claim(self):
        status, stdout, stderr = self.run_cli()
        self.assertEqual(status, 0, stderr)
        self.assertIn("; deb only;", stdout)
        self.assertNotIn("npm only", stdout)

    def test_mixed_ecosystems_cannot_collide_on_name_and_version(self):
        npm = {"type": "library", "name": "openssl", "version": "3.0.9-1", "purl": "pkg:npm/openssl@3.0.9-1"}
        self.bom["components"].append(npm)
        self.expected.write_text(json.dumps(self.bom))
        self.generated["components"].append(npm)
        self.native["Results"].append({"Class": "lang-pkgs", "Type": "node-pkg",
                                        "Packages": [{"Name": "openssl", "Version": "3.0.9-1"}]})
        receipt = self.success()
        self.assertEqual(receipt["packages"], 2)
        self.assertEqual(receipt["coverage"]["ecosystems"], ["deb", "npm"])

    def test_epoch_release_and_architecture_are_preserved(self):
        for number, arch in enumerate(["amd64", "all"]):
            purl = f"pkg:deb/debian/openssl@3.0.9-1?arch={arch}&distro=debian-12.0&epoch=2"
            self.output = self.root / ("epoch-" + str(number))
            self.bom["components"][1].update(version="2:3.0.9-1", purl=purl)
            self.expected.write_text(json.dumps(self.bom))
            self.generated["components"][1].update(version="2:3.0.9-1", purl=purl)
            self.native["Results"][0]["Packages"][0].update(Epoch=2, Arch=arch, Identifier={"PURL": purl})
            self.native["Results"][0]["Vulnerabilities"][0].update(InstalledVersion="2:3.0.9-1", PkgIdentifier={"PURL": purl})
            with self.subTest(architecture=arch):
                self.assertEqual(self.success()["packages"], 1)

    def test_expected_inventory_rejects_ambiguous_or_unsupported_purls(self):
        cases = [self.purl.replace("debian/", "ubuntu/"), self.purl.replace("debian-12.0", "debian-11"),
                 self.purl.replace("amd64", "arm64"), self.purl.replace("&distro=debian-12.0", ""),
                 self.purl + "&arch=amd64", self.purl + "&unknown=value", self.purl + "#file",
                 self.purl + "&epoch=2", self.purl.replace("3.0.9-1", "3.0.9-2")]
        for number, purl in enumerate(cases):
            self.output = self.root / ("bad-purl-" + str(number))
            bom = copy.deepcopy(self.bom)
            bom["components"][1]["purl"] = purl
            self.expected.write_text(json.dumps(bom))
            self.commands.clear()
            with self.subTest(purl=purl):
                self.assertEqual(self.run_cli()[0], 1)
                self.assertFalse(self.commands)
                self.assertFalse((self.output / "receipt.json").exists())

    def test_large_qualifier_list_fails_within_memory_budget_before_scanner(self):
        bom = copy.deepcopy(self.bom)
        bom["components"][1]["purl"] = self.purl + "&a=b" * 1_500_000
        self.expected.write_text(json.dumps(bom))
        self.assertLess(self.expected.stat().st_size, 10 * 1024 * 1024)
        marker = self.root / "scanner-invoked"
        self.trivy.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n")
        self.trivy.chmod(0o700)
        runner = ("import resource, runpy, sys; "
                  "resource.setrlimit(resource.RLIMIT_AS, (192 * 1024 * 1024, 192 * 1024 * 1024)); "
                  "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')")
        command = [sys.executable, "-c", runner, str(ROOT / "scripts/scan-image-advisory.py"),
                   "--image-archive", str(self.archive), "--expected-sbom", str(self.expected),
                   "--trivy", str(self.trivy), "--cache-dir", str(self.cache), "--output-dir", str(self.output)]
        result = subprocess.run(command, env={"HOME": str(self.root)}, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("IMAGE_ADVISORY_ERROR: collection failed;", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse((self.output / "receipt.json").exists())

    def test_expected_os_component_must_be_unique_supported_and_consistent(self):
        base = copy.deepcopy(self.bom)
        cases = [lambda b: b["components"].pop(0),
                 lambda b: b["components"].append(copy.deepcopy(b["components"][0])),
                 lambda b: b["components"][0].update(version="12.1"),
                 lambda b: b["components"][0].update(name="ubuntu"),
                 lambda b: b["components"].pop(1)]
        for number, mutate in enumerate(cases):
            bom = copy.deepcopy(base)
            mutate(bom)
            self.expected.write_text(json.dumps(bom))
            self.output = self.root / ("expected-os-" + str(number))
            self.commands.clear()
            with self.subTest(case=number):
                self.assertEqual(self.run_cli()[0], 1)
                self.assertFalse(self.commands)

    def test_native_os_and_full_package_identity_must_match(self):
        cases = [lambda: self.native["Metadata"].pop("OS"),
                 lambda: self.native["Metadata"]["OS"].update(Family="ubuntu"),
                 lambda: self.native["Metadata"]["OS"].update(Name="12.1"),
                 lambda: self.native["Metadata"]["OS"].update(Eosl=True),
                 lambda: self.native["Results"][0].update(Type="ubuntu"),
                 lambda: self.native["Results"][0].update(Packages=[]),
                 lambda: self.native["Results"][0]["Packages"][0].update(Release="2"),
                 lambda: self.native["Results"][0]["Packages"][0].update(Epoch=2),
                 lambda: self.native["Results"][0]["Packages"][0].update(Arch="all"),
                 lambda: self.native["Results"][0]["Packages"][0].update(AnalyzedBy="rpm"),
                 lambda: self.native["Results"][0]["Packages"][0].update(Identifier={}),
                 lambda: self.native["Results"][0]["Vulnerabilities"][0].update(InstalledVersion="3.0.9-2"),
                 lambda: self.native["Results"][0]["Vulnerabilities"][0].update(PkgIdentifier={"PURL": self.purl.replace("amd64", "all")})]
        baseline = copy.deepcopy(self.native)
        for number, mutate in enumerate(cases):
            self.native = copy.deepcopy(baseline)
            self.output = self.root / ("native-" + str(number))
            mutate()
            with self.subTest(case=number):
                self.assertEqual(self.run_cli()[0], 1)
                self.assertFalse((self.output / "receipt.json").exists())

    def test_debian_image_requires_supported_platform_metadata(self):
        baseline = copy.deepcopy(self.native)
        for number, config in enumerate([None, {}, {"architecture": "arm64", "os": "linux"},
                                         {"architecture": "amd64", "os": "windows"}]):
            self.native = copy.deepcopy(baseline)
            self.native["Metadata"]["ImageConfig"] = config
            self.output = self.root / ("platform-" + str(number))
            with self.subTest(config=config):
                self.assertEqual(self.run_cli()[0], 1)
                self.assertFalse((self.output / "receipt.json").exists())

    def test_generated_sbom_cannot_omit_add_or_change_os_packages(self):
        cases = [lambda: self.generated["components"].pop(),
                 lambda: self.generated["components"].pop(0),
                 lambda: self.generated["components"][0].update(version="12.1"),
                 lambda: self.generated["components"][1].update(purl=self.purl.replace("amd64", "all")),
                 lambda: self.generated["components"].append({"type": "library", "name": "extra", "version": "1", "purl": "pkg:npm/extra@1"})]
        baseline = copy.deepcopy(self.generated)
        for number, mutate in enumerate(cases):
            self.generated = copy.deepcopy(baseline)
            self.output = self.root / ("generated-" + str(number))
            mutate()
            with self.subTest(case=number):
                self.assertEqual(self.run_cli()[0], 1)
                self.assertFalse((self.output / "receipt.json").exists())

    def test_debian_does_not_broaden_standalone_sbom_mode(self):
        self.assertEqual(self.run_cli(standalone=True)[0], 1)
        self.assertFalse(self.commands)

    def test_stale_database_and_scanner_failure_never_become_clean(self):
        metadata = self.cache / "db/metadata.json"
        original = metadata.read_bytes()
        data = json.loads(original)
        data["NextUpdate"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        metadata.write_text(json.dumps(data))
        self.assertEqual(self.run_cli()[0], 1)
        self.assertFalse(self.commands)
        metadata.write_bytes(original)
        for number, fmt in enumerate(["json", "cyclonedx"]):
            self.output = self.root / ("scanner-failure-" + str(number))
            def process(command, **kwargs):
                if "--format" in command and command[command.index("--format") + 1] == fmt:
                    return subprocess.CompletedProcess(command, 7)
                return self.process(command, **kwargs)
            with self.subTest(format=fmt):
                self.assertEqual(self.run_cli(process)[0], 1)
                self.assertFalse((self.output / "receipt.json").exists())


if __name__ == "__main__":
    unittest.main()
