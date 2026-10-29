"""Collect advisory npm evidence from an explicit local SBOM or image archive."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote


VERSION = "0.70.0"
MAX_JSON = 10 * 1024 * 1024
MAX_ARCHIVE = 64 * 1024 * 1024


def require(condition):
    if not condition:
        raise ValueError("invalid, unsupported or incomplete advisory evidence")


def digest(path):
    require(path.is_file())
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def parse(raw):
    require(0 < len(raw) <= MAX_JSON)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: require(False))
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            require(depth <= 32)
            if isinstance(item, (dict, list)):
                require(len(item) <= 10000)
                pending.extend((child, depth + 1) for child in (item.values() if isinstance(item, dict) else item))
        require(isinstance(value, dict))
        return value
    except (UnicodeError, RecursionError) as error:
        raise ValueError("invalid advisory JSON") from error


def read_raw(path):
    require(path.is_file() and path.stat().st_size <= MAX_JSON)
    return path.read_bytes()


def read_json(path):
    return parse(read_raw(path))


def inventory(bom):
    require(bom.get("bomFormat") == "CycloneDX" and bom.get("specVersion") in ("1.5", "1.6"))
    require(type(bom.get("version")) is int and bom["version"] >= 1)
    components = bom.get("components")
    require(isinstance(components, list) and 0 < len(components) <= 10000)
    packages = set()
    for component in components:
        require(isinstance(component, dict) and component.get("type") == "library")
        require(not component.get("components"))
        name, version, group = (component.get(key, "") for key in ("name", "version", "group"))
        require(all(isinstance(value, str) for value in (name, version, group)))
        name = group + "/" + name if group else name
        require(re.fullmatch(r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+", name) is not None)
        require(re.fullmatch(r"[A-Za-z0-9._+~-]+", version) is not None)
        purl = component.get("purl")
        require(isinstance(purl, str) and purl.startswith("pkg:npm/"))
        require(unquote(purl[8:]) == name + "@" + version)
        require((name, version) not in packages)
        packages.add((name, version))
    return packages


def native_summary(report, expected, artifact_type="cyclonedx"):
    require(type(report.get("SchemaVersion")) is int and report["SchemaVersion"] == 2)
    require(report.get("Trivy") == {"Version": VERSION} and report.get("ArtifactType") == artifact_type)
    results = report.get("Results")
    require(isinstance(results, list) and bool(results))
    packages, findings = set(), 0
    severities = {key: 0 for key in ("UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL")}
    for result in results:
        require(isinstance(result, dict) and result.get("Class") == "lang-pkgs" and result.get("Type") in ("npm", "node-pkg"))
        require(not any(result.get(key) for key in ("Misconfigurations", "Secrets", "Licenses", "CustomResources", "ExperimentalModifiedFindings")))
        rows = result.get("Packages")
        require(isinstance(rows, list) and bool(rows))
        for package in rows:
            require(isinstance(package, dict))
            pair = package.get("Name"), package.get("Version")
            require(all(isinstance(item, str) and item for item in pair))
            packages.add(pair)
        vulnerabilities = result.get("Vulnerabilities", [])
        require(isinstance(vulnerabilities, list))
        for finding in vulnerabilities:
            require(isinstance(finding, dict))
            pair = finding.get("PkgName"), finding.get("InstalledVersion")
            require(all(isinstance(item, str) for item in pair) and pair in expected)
            require(isinstance(finding.get("VulnerabilityID"), str) and bool(finding["VulnerabilityID"]))
            severity = finding.get("Severity")
            require(isinstance(severity, str) and severity in severities)
            severities[severity] += 1
            findings += 1
    require(packages == expected)
    return {"packages": len(packages), "findings": findings, "severities": severities}


def archive_identity(report, bom, expected, snapshot):
    """Correlate native outputs; an image config ID is not a registry manifest ID."""
    metadata = report.get("Metadata")
    require(isinstance(metadata, dict) and not metadata.get("OS"))
    image_id = metadata.get("ImageID")
    require(isinstance(image_id, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is not None)
    require(report.get("ArtifactName") == str(snapshot))
    require(inventory(bom) == expected)
    metadata = bom.get("metadata")
    require(isinstance(metadata, dict))
    tools = metadata.get("tools")
    require(isinstance(tools, dict) and isinstance(tools.get("components"), list))
    require(len(tools["components"]) == 1)
    tool = tools["components"][0]
    require(isinstance(tool, dict) and tool.get("name") == "trivy" and tool.get("version") == VERSION)
    component = metadata.get("component")
    require(isinstance(component, dict) and component.get("type") == "container")
    require(component.get("name") == str(snapshot))
    properties = component.get("properties")
    require(isinstance(properties, list) and all(isinstance(item, dict) for item in properties))
    require([item.get("value") for item in properties if item.get("name") == "aquasecurity:trivy:ImageID"] == [image_id])
    return image_id


def scan(sbom, trivy, cache, output, *, image_archive=None):
    """Return a receipt only after native success and complete supported inventory."""
    sbom, trivy, cache, output = (Path(path).absolute() for path in (sbom, trivy, cache, output))
    raw_sbom = read_raw(sbom)
    bom = parse(raw_sbom)
    expected = inventory(bom)
    raw_archive = None
    if image_archive is not None:
        image_archive = Path(image_archive).absolute()
        require(image_archive.is_file())
        with image_archive.open("rb") as stream:
            raw_archive = stream.read(MAX_ARCHIVE + 1)
        require(0 < len(raw_archive) <= MAX_ARCHIVE)
    metadata_path, database = cache / "db/metadata.json", cache / "db/trivy.db"
    raw_metadata = read_raw(metadata_path)
    metadata = parse(raw_metadata)
    require(type(metadata.get("Version")) is int and metadata["Version"] == 2)
    started = datetime.now(timezone.utc)
    dates = []
    for key in ("UpdatedAt", "NextUpdate", "DownloadedAt"):
        require(isinstance(metadata.get(key), str))
        stamp = datetime.fromisoformat(metadata[key].replace("Z", "+00:00"))
        require(stamp.tzinfo is not None)
        dates.append(stamp)
    require(dates[0] <= started < dates[1] and dates[0] <= dates[2] <= started)
    identities = {path: digest(path) for path in (trivy, database)}
    identities[metadata_path] = hashlib.sha256(raw_metadata).hexdigest()
    if image_archive is not None:
        identities[image_archive] = hashlib.sha256(raw_archive).hexdigest()
        identities[sbom] = hashlib.sha256(raw_sbom).hexdigest()
    output.mkdir(mode=0o700)

    def write(name, value):
        with (output / name).open("xb") as stream:
            os.chmod(stream.name, 0o600)
            stream.write(value)
        identities[output / name] = hashlib.sha256(value).hexdigest()

    # The snapshot, not the caller's mutable input path, is the scanned subject.
    write("expected-sbom.cdx.json" if image_archive is not None else "sbom.cdx.json", raw_sbom)
    subject_sha256 = hashlib.sha256(raw_sbom).hexdigest()
    if image_archive is not None:
        write("image.tar", raw_archive)
        subject_sha256 = identities[image_archive]
    write("db-metadata.json", raw_metadata)
    write("trivy.yaml", b"{}\n")
    write("ignore", b"")
    (output / "home").mkdir(mode=0o700)
    env = {"HOME": str(output / "home"), "PATH": os.defpath}
    base = [str(trivy), "--cache-dir", str(cache), "--config", str(output / "trivy.yaml")]

    def run(command, log):
        require(digest(trivy) == identities[trivy])
        with (output / log).open("xb") as stdout, (output / (log + ".stderr")).open("xb") as stderr:
            os.chmod(stdout.name, 0o600)
            os.chmod(stderr.name, 0o600)
            result = subprocess.run(command, cwd=output, env=env, stdin=subprocess.DEVNULL,
                                    stdout=stdout, stderr=stderr, timeout=60, check=False)
        require(result.returncode == 0)

    run(base + ["--version"], "version.txt")
    require((output / "version.txt").stat().st_size <= 16384)
    version_lines = (output / "version.txt").read_text().splitlines()
    require(bool(version_lines) and version_lines[0] == "Version: " + VERSION)
    target = ["image", "--input", str(output / "image.tar")] if image_archive is not None else ["sbom"]
    command = base + target + ["--scanners", "vuln", "--format", "json", "--list-all-pkgs",
                      "--exit-code", "0", "--offline-scan", "--skip-db-update", "--skip-java-db-update",
                      "--skip-version-check", "--disable-telemetry", "--ignorefile", str(output / "ignore"),
                      "--timeout", "50s", "--output", str(output / "trivy.json")]
    if image_archive is None:
        command.append(str(output / "sbom.cdx.json"))
    run(command, "scanner.stdout")
    os.chmod(output / "trivy.json", 0o600)
    raw_report = read_raw(output / "trivy.json")
    report = parse(raw_report)
    identities[output / "trivy.json"] = hashlib.sha256(raw_report).hexdigest()
    summary = native_summary(report, expected, "container_image" if image_archive is not None else "cyclonedx")
    archive_evidence = {}
    subject = {"kind": "sbom", "sha256": subject_sha256, "image_association": "unverified"}
    if image_archive is not None:
        sbom_command = command.copy()
        sbom_command[sbom_command.index("--format") + 1] = "cyclonedx"
        sbom_command[sbom_command.index("--output") + 1] = str(output / "generated-sbom.cdx.json")
        run(sbom_command, "sbom.stdout")
        os.chmod(output / "generated-sbom.cdx.json", 0o600)
        raw_generated = read_raw(output / "generated-sbom.cdx.json")
        image_id = archive_identity(report, parse(raw_generated), expected, output / "image.tar")
        identities[output / "generated-sbom.cdx.json"] = hashlib.sha256(raw_generated).hexdigest()
        subject = {"kind": "image-archive", "sha256": subject_sha256,
                   "config_digest": image_id, "config_digest_basis": "scanner-reported",
                   "registry_manifest_digest": None, "deployed_image_association": "unverified"}
        archive_evidence = {"generated_sbom_sha256": identities[output / "generated-sbom.cdx.json"],
                            "expected_inventory_sha256": identities[sbom],
                            "sbom_association": "generated-from-same-archive",
                            "coverage": {"ecosystems": ["npm"], "whole_image": "not-established"}}
    require(all(digest(path) == identity for path, identity in identities.items()))
    receipt = {"schema_version": 1, "mode": "advisory", "collection_status": "ok",
               "security_verdict": "not-established", "started_at": started.isoformat(),
               "finished_at": datetime.now(timezone.utc).isoformat(),
               "subject": subject,
               "scanner": {"version": VERSION, "sha256": identities[trivy]},
               "database": {"sha256": identities[database], "metadata_sha256": identities[metadata_path], "metadata": metadata},
               "report_sha256": identities[output / "trivy.json"], **summary, **archive_evidence}
    write("receipt.json", (json.dumps(receipt, indent=2) + "\n").encode())
    return receipt


def scan_archive(archive, expected_sbom, trivy, cache, output):
    """Scan only the explicit local archive; never extract or execute it here."""
    return scan(expected_sbom, trivy, cache, output, image_archive=archive)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--sbom", type=Path)
    target.add_argument("--image-archive", type=Path)
    parser.add_argument("--expected-sbom", type=Path)
    for option in ("trivy", "cache-dir", "output-dir"):
        parser.add_argument("--" + option, type=Path, required=True)
    args = parser.parse_args(argv)
    if (args.image_archive is not None) != (args.expected_sbom is not None):
        parser.error("--expected-sbom is required only with --image-archive")
    try:
        if args.image_archive is not None:
            receipt = scan_archive(args.image_archive, args.expected_sbom, args.trivy, args.cache_dir, args.output_dir)
        else:
            receipt = scan(args.sbom, args.trivy, args.cache_dir, args.output_dir)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("IMAGE_ADVISORY_ERROR: collection failed; no security verdict (" + type(error).__name__ + ").", file=sys.stderr)
        return 1
    if args.image_archive is not None:
        print("IMAGE_ARCHIVE_ADVISORY_OK: findings=" + str(receipt["findings"]) + "; npm only; deployed image association unverified.")
    else:
        print("IMAGE_ADVISORY_OK: findings=" + str(receipt["findings"]) + "; SBOM only; image association unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
