# Enrollment certificate ownership

**Status:** S06 guard/source preparation; no manager deployment or certificate rotation.

Desktop's Orion canary now prepares a pin for the existing public authd certificate.
It was inspected through authenticated `kubectl --context homelab` on 2026-11-21:
subject/issuer `CN=wazuh-manager-master-0`, no SAN/extensions, valid 2026-09-21
through 2036-09-18, SHA-256 fingerprint
`3D:53:33:FB:58:6F:22:DA:2A:72:71:23:9F:F4:3A:EB:B0:F9:20:3F:35:2D:0A:66:76:94:00:B4:34:9B:05:27`.
No private key or runtime password was inspected or copied.

GitOps owns the manager's persisted certificate/key and recovery. They are under
`/var/ossec/etc`, mounted from `wazuh-manager-master-wazuh-manager-master-0`,
subpath `wazuh/var/ossec/etc`. Source and live pod metadata agree. The existing
single-master registration service remains unchanged. `tests/wazuh-contract.sh`
now guards that routing, mount and authd password/certificate configuration.

Desktop owns its public-only PEM pin and registration-only hostname mapping.
Any master-name, certificate, expiry or PVC recovery change needs coordinated
review of that pin before enrollment resumes. Do not use the unrelated
indexer/dashboard CA, run the broad bootstrap-secret rotation recipe, delete
client keys, or turn verification off to conceal an enrollment failure. No
manager overlay change is needed for this existing stable certificate.

The pinned agent and manager are 4.14.7. Desktop's disposable actual-agent tests
verify trusted acceptance, untrusted/wrong-name rejection before credentials,
missing-anchor failure and the current CN-only self-signed certificate shape.
Its container initialization tests preserve synthetic identity bytes and SSH
configuration. These do not prove the standing Orion agent restarted correctly;
coordinator-owned live trust, SSH recovery and identity receipts remain open.

Local GitOps verification: `bash tests/wazuh-contract.sh` passed using an existing
Python environment with PyYAML. First invocation lacked PyYAML and failed before
the new guards; this was an environment failure, not a RED assertion. Existing
persistence behavior already passed these regression guards; no invented
RED/GREEN claim is made for unchanged manager behavior.

S07 remains gated on S06 live proof. The initial proposed host fixture is a
narrow read-only watched directory containing harmless configuration-shaped
text, with an unwatched control, no file diffs and no private host content.
Index retention, tested restore, heartbeat recovery, attributable mutation/
restoration notifications and seven-day capacity observations remain separate
acceptance work. No index policy, FIM rule or fleet enrollment changed here.

Rollback for this source-only guard is reverting the guard/docs change; it has
no runtime effect. Restoring the manager PVC or rotating certificates is a
separately authorized operation, not part of this preparation.

Fresh independent source review passed and reproduced all 14 Desktop tests.
The coordinator-authorized live TLS-only probe verified the current manager with
this public pin and exact hostname under TLS 1.2 (verification return code 0,
ECDHE-RSA-AES256-GCM-SHA384). It sent no enrollment request or credentials and
did not mutate standing identity. Deployed Orion recovery remains unproved.

The initial S07 retention decision was pending when this receipt began. The user
subsequently answered “do as recomendation”, accepting thirty-day alert retention,
daily encrypted index and manager-state backups, and isolated restore. The
coordinator selected the existing Kepler-local encrypted Restic repository as the
primary destination; Voyager expansion remains separate. This S06 change applies
no retention policy and provisions no credential. The S07 recovery source and
actual isolated-restore evidence remain separate delivery gates.


## Review correction

Hackerman's two P2 findings at `4420d94` were reproduced with rendered manifest
mutations: renaming the master's selector/template label and removing its authd
password volume/mount both incorrectly passed the original contract. The guard
now compares the exact registration selector with the master Pod labels and
requires the named Secret-backed, read-only password mount and subPath. Both
mutations now fail while the unchanged rendered deployment passes. No live
manifest or Secret changed. Existing `scripts/validate.sh` already runs this
contract in CI; no second workflow or deployment path was introduced.
