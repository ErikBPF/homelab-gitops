# Local advisory evidence: SBOM and image archive pilots

The accepted policy is **“Advisory evidence first (recommended)”** (question
`call_95bfd504bf844137a65ece1f4ba9749e`, item 0, coordination revision 14).
Vulnerabilities do not block promotion. Scanner failures remain failures.
The SBOM mode scans an explicitly supplied local CycloneDX 1.5/1.6 npm
inventory with Trivy 0.70.0. Its immutable subject is the SBOM byte digest;
association with an image digest remains **unverified**. It establishes neither
whole-image coverage nor a production security verdict.
The opt-in archive mode below generates its own SBOM from the same local
archive used for vulnerability reporting. Registry and deployed-image
association remain unverified.

## Run locally

Use Python 3.11 or later, a separately verified Trivy 0.70.0 executable, and an
explicit Trivy cache containing a current schema-2 vulnerability database.
The command does not install tools or update the database. Supply a new output
directory; existing evidence is never overwritten.

```sh
python3 scripts/scan-image-advisory.py \
  --sbom tests/fixtures/image-advisory/vulnerable.cdx.json \
  --trivy /absolute/path/to/verified/trivy \
  --cache-dir /absolute/path/to/disposable/trivy-cache \
  --output-dir /absolute/path/to/new-private-evidence
```

The command returns zero only after successful collection and validation. Any
severity, including CRITICAL, remains advisory. A fresh output bundle retains
the original SBOM, native `trivy.json`, DB metadata, scanner logs and
`receipt.json`. The receipt records scanner version and executable SHA256, DB
and metadata SHA256, DB timestamps, subject and report SHA256, package count,
and severity totals. Opaque native descriptions and paths stay in the private
bundle; console output contains only aggregate status.

The wrapper uses an isolated HOME, empty configuration and ignore file, a
minimal subprocess environment, explicit `sbom` input, all vulnerability
severities, and offline/update/telemetry suppression flags. It does not select
the host tree, container daemon or registry. Each subprocess has a 60-second
timeout. JSON is bounded to 10 MiB with duplicate-key and depth checks. Empty
inventories, other ecosystems, unsupported schemas, missing package coverage,
expired or malformed DB metadata, changed scanner/DB/SBOM bytes and native
errors fail without a success receipt. Every supplied npm name/version must
appear in the native package inventory. Nested component inventories are not
supported. A zero-finding report still says `security_verdict: not-established`.

## Verification and retained evidence

`python3 tests/test_image_advisory.py` is the offline contract, wired into the
existing `scripts/validate.sh`. Synthetic process replies exercise collection
and validation boundaries; they do not prove scanner detection. Existing
digest, Harbor-routing and rendering gates remain in the owner runner.

On 2026-10-29 the real scanner processed both committed synthetic SBOM fixtures:

| Control | Native result | Wrapper exit |
| --- | --- | --- |
| `lodash@4.17.11` | 7 findings: 1 CRITICAL, 3 HIGH, 3 MEDIUM | 0 |
| `left-pad@1.3.0` | 1 package, 0 findings | 0 |
| Valid control SBOM with deliberately corrupt disposable DB | Native DB error; no success receipt | 1 |

Both successful runs used database `UpdatedAt=2026-10-29T13:09:46.269823229Z`,
SHA256 `162d88bff697bee519561c90bb5766f2b9d27e3f0f0eb051b7a3d7bf1df42ff7`.
Trivy warned that third-party SBOMs can reduce detection accuracy. The control
means no findings in that supplied inventory and DB, not a compliant image.

Initial RED: 10 tests, 26 assertion failures against the importable stub.
The first test run also exposed a fixture-reset error, corrected before the
clean RED. GREEN: 10 tests. Two later regression assertions caught empty
version output and mutation of the scanned snapshot. Native execution then
exposed the valid `node-pkg` result type; its regression failed before the
minimal compatibility fix. Final focused result: 13 tests passed.

Raw receipts, failed native attempts, successful bundles and frozen drafts
remain under `.local/image-advisory/`, protected by a narrow local Git exclude
and private permissions. Draft 1 archive SHA256:
`a7746e866c948ff7ec54e70bef3abc72f365b7cea3c4a2ff89a5984919ab7344`.
Full owner validation and independent review are coordinated separately.

## Local Docker archive mode

Use `--image-archive` with `--expected-sbom` to scan a trusted, explicitly
selected local Docker archive. The expected CycloneDX file declares the npm
name/version inventory that both native outputs must contain. The runner
generates a separate native CycloneDX SBOM from the archive; a supplied SBOM
alone does not establish image association.

Generate the tiny synthetic controls in a new disposable directory:

```sh
python3 tests/fixtures/image-advisory/build_archives.py /absolute/path/to/new-fixtures
python3 scripts/scan-image-advisory.py \
  --image-archive /absolute/path/to/new-fixtures/vulnerable.docker.tar \
  --expected-sbom tests/fixtures/image-advisory/vulnerable.cdx.json \
  --trivy /absolute/path/to/verified/trivy \
  --cache-dir /absolute/path/to/disposable/trivy-cache \
  --output-dir /absolute/path/to/new-private-evidence
```

The immutable subject is the archive byte SHA256. The config digest is
explicitly scanner-reported and must agree between the native JSON and generated
SBOM. It is distinct from the archive digest, Trivy's opaque artifact ID and a
registry manifest digest. No registry manifest or deployed-image identity is
established. The private bundle retains the archive, expected inventory,
generated SBOM, native report and collection metadata.

This pilot accepts archives up to 64 MiB and npm-only coverage. Empty,
incomplete, extra, OS or other ecosystem inventories fail collection. Source,
scanner, database, snapshot, configuration or previously written report
mutation also prevents a success receipt. JSON and subprocess limits match the
SBOM mode. Findings at every severity remain advisory; successful collection
prints `IMAGE_ARCHIVE_ADVISORY_OK`, while collection errors return nonzero.

`python3 tests/test_image_archive_advisory.py` checks these boundaries alongside
the unchanged SBOM tests in the owner validator. The fixture generator creates
only synthetic package metadata; it never pulls or runs a container. Native
controls should run in a disposable network namespace. The wrapper does not
extract archives or select a daemon or registry. Native Trivy accepts Docker
archives and OCI directories; OCI tar input is outside this mode's tested path.

Native local verification used the two generated 20 KiB archives: lodash
produced seven findings and the left-pad control produced none; both collections
succeeded. Corrupt archive and corrupt database controls returned nonzero and
wrote no success receipt. All four ran in isolated user/network namespaces.
The eleven archive contracts first recorded 35 assertion failures against an
importable stub, then passed; the thirteen existing SBOM contracts pass unchanged.
The retained RED archive has SHA256
`a54ecc32e823b3f410659a41bfa930228cbcbe36c999a2e5166d38764c8aa018`;
the importable stub has SHA256
`f77d10f3df5899290ec01dd43aff17e59883d7dacae3e2c2d94ab7f28a9cde0f`.
Independent review then added permanent timeout coverage at both native stages.
Those assertions already passed against the implementation; they are coverage
completion, not part of the initial RED.

The full local owner validator passed 22 charts, six Kustomizations and 39
contract entries. Schema validation events were 515 valid, zero invalid or
erroneous, and 175 skipped for missing schemas; repeated rendered objects mean
these are not counts of unique resources. The timeout-only test extension happened
during that run; final focused tests and syntax checks passed separately.
Hosted validation checks the complete final commit. Rendering and these
synthetic controls do not prove deployment or production runtime behavior.

## Provenance and remaining work

The [official 0.70.0 release](https://github.com/aquasecurity/trivy/releases/tag/v0.70.0)
is marked immutable, published April 17, after the
[March supply-chain incident](https://github.com/aquasecurity/trivy/security/advisories/GHSA-69fq-xp46-6x23).
The incident's attempted malicious 0.70.0 branch produced no release or tag.
The downloaded Linux archive matched its pinned release checksum
`8b4376d5d6befe5c24d503f10ff136d9e0c49f9127a4279fd110b727929a5aa9`;
executable SHA256 is
`379d59f24a4a828c55de5f0b91b6805cc35d13580180b658820e648611256166`.
Signature/attestation verification was not performed. The official default
database mirror supplied the DB; its bytes and timestamps are retained, without
claiming independent DB authenticity verification.

Native validation follows the pinned upstream
[report schema](https://github.com/aquasecurity/trivy/blob/v0.70.0/pkg/types/report.go),
[package types](https://github.com/aquasecurity/trivy/blob/v0.70.0/pkg/fanal/types/const.go)
and [report flags](https://github.com/aquasecurity/trivy/blob/v0.70.0/pkg/flag/report_flags.go).
The existing agent-evals declarative adapter accepts configuration findings;
it is not reused or expanded into an image report platform here.

Production image association, OS and other ecosystems, registry credentials,
scheduled rescans, promotion thresholds, exceptions and provenance signatures
remain outside this pilot. No production workflow or deployment was changed.
Recovery is to remove this local command and its offline runner entry; retain
evidence and existing digest gates. A failed bundle may be examined privately;
rerun into a new directory after correcting the collection problem.
