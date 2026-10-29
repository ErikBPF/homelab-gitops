"""Scan an explicit local npm CycloneDX SBOM; never infer image provenance."""

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


def native_summary(report, expected):
    require(type(report.get("SchemaVersion")) is int and report["SchemaVersion"] == 2)
    require(report.get("Trivy") == {"Version": VERSION} and report.get("ArtifactType") == "cyclonedx")
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


def scan(sbom, trivy, cache, output):
    """Return a receipt only after native success and complete supported inventory."""
    sbom, trivy, cache, output = (Path(path).absolute() for path in (sbom, trivy, cache, output))
    raw_sbom = read_raw(sbom)
    bom = parse(raw_sbom)
    expected = inventory(bom)
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
    output.mkdir(mode=0o700)

    def write(name, value):
        with (output / name).open("xb") as stream:
            os.chmod(stream.name, 0o600)
            stream.write(value)

    # The snapshot, not the caller's mutable input path, is the scanned subject.
    write("sbom.cdx.json", raw_sbom)
    subject_sha256 = hashlib.sha256(raw_sbom).hexdigest()
    write("db-metadata.json", raw_metadata)
    write("trivy.yaml", b"{}\n")
    write("ignore", b"")
    (output / "home").mkdir(mode=0o700)
    env = {"HOME": str(output / "home"), "PATH": os.defpath}
    base = [str(trivy), "--cache-dir", str(cache), "--config", str(output / "trivy.yaml")]

    def run(command, log):
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
    command = base + ["sbom", "--scanners", "vuln", "--format", "json", "--list-all-pkgs",
                      "--exit-code", "0", "--offline-scan", "--skip-db-update", "--skip-java-db-update",
                      "--skip-version-check", "--disable-telemetry", "--ignorefile", str(output / "ignore"),
                      "--timeout", "50s", "--output", str(output / "trivy.json"), str(output / "sbom.cdx.json")]
    run(command, "scanner.stdout")
    os.chmod(output / "trivy.json", 0o600)
    summary = native_summary(read_json(output / "trivy.json"), expected)
    require(all(digest(path) == identity for path, identity in identities.items()))
    require(digest(output / "sbom.cdx.json") == subject_sha256)
    receipt = {"schema_version": 1, "mode": "advisory", "collection_status": "ok",
               "security_verdict": "not-established", "started_at": started.isoformat(),
               "finished_at": datetime.now(timezone.utc).isoformat(),
               "subject": {"kind": "sbom", "sha256": subject_sha256, "image_association": "unverified"},
               "scanner": {"version": VERSION, "sha256": identities[trivy]},
               "database": {"sha256": identities[database], "metadata_sha256": identities[metadata_path], "metadata": metadata},
               "report_sha256": digest(output / "trivy.json"), **summary}
    write("receipt.json", (json.dumps(receipt, indent=2) + "\n").encode())
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("sbom", "trivy", "cache-dir", "output-dir"):
        parser.add_argument("--" + option, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = scan(args.sbom, args.trivy, args.cache_dir, args.output_dir)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("IMAGE_ADVISORY_ERROR: collection failed; no security verdict (" + type(error).__name__ + ").", file=sys.stderr)
        return 1
    print("IMAGE_ADVISORY_OK: findings=" + str(receipt["findings"]) + "; SBOM only; image association unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
