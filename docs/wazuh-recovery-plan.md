# Wazuh recovery: source preparation before activation

## Outcome and why

The user accepted “do as recomendation”: 30-day alert retention, daily encrypted
index snapshots and manager-state backup, and an isolated restore using the
existing approved destination. This candidate prepares recovery before deletion.
No live snapshot, manager stop, retention attachment or restore is claimed.
Suspension disables only scheduled capture: merging and syncing this candidate
changes StatefulSet hooks, initialization and indexer configuration, potentially
rolling workloads. Root must sequence that reviewed rollout explicitly.

Kepler’s existing encrypted Restic repository is the primary destination. Servarr
owns its credential and transport; Desktop owns bounded manager capture and the
installed transport artifact. Voyager replication remains a later increment:
limited offsite capacity does not block the accepted local backup. Kepler storage
and backup share a failure domain, so local recovery does not protect against loss
of that machine or pool.

## What changes and how it helps

GitOps adds native filesystem snapshot configuration on the existing indexer PVC,
a suspended daily producer, and a separate projected OpenSearch identity. Only
complete native snapshots with successful shards become immutable artifacts.
The producer reads no manager token or private administrator certificate. Its
cluster-level snapshot permissions include deletion but not restore or index
deletion; OpenSearch cannot restrict those permissions to one repository. Exact
repository/name checks are application controls, not server-enforced isolation.

Servarr [PR362](https://github.com/ErikBPF/servarr/pull/362) verifies producer hashes,
backs up a stable bundle through the existing Restic container, and reads back the
native archive before publishing cleanup proof. Root-owned public proof metadata
lives outside the indexer PVC at `/bulk/k8s/wazuh-recovery-proof`. The producer mounts
it read-only without fsGroup. Parent and file ownership/mode checks reject writable
or substituted proof. Only the acknowledged native snapshot and its staging
artifact can be retired; encrypted archives remain outside producer authority.

The manager controller gets only named scale endpoints and required read-only
Pod/PVC/PV/Application metadata. Argo delegates exactly two manager replica fields;
no broad ignore or global synchronization suppression is added. Both manager
preStop hooks invalidate old markers, invoke the official stop command, verify
process absence, and persist the current Pod UID. Desktop requires these proofs
before its task-owned ZFS capture and always attempts resumption. Manager database
consistency is claimed only after these checks; Filebeat consistency remains
unproved. Independent capture intervals prevent a false atomic cross-component
recovery point.

The isolated restore command checks hashes, real SQLite integrity, the pinned
public authd certificate and private-key correspondence, then restores index data
into a pinned container with no network. Historical verified backups remain
restorable; output reports source age rather than rejecting old recovery points.
The 30-day policy is stored unattached, without an automatic index template.

## Evidence and limits

Read-only metadata verified existing PVC bindings, pinned 4.14.7 images, empty
native repository inventory, Kepler local repository and free capacity. NFS and
ZFS staging are plaintext within the existing storage boundary; Restic encrypts
the backup. The public proof mount adds no payload or credential exposure. Existing
NFS trust and root-squash assumptions remain part of the host boundary.

Independent review passed after two reproduced resource-bound repairs: streaming
subprocess output is capped before allocation, and archive headers stop at the
member limit. Sixteen focused tests pass. Offline checks cover rendered RBAC, Argo delegation, suspended scheduling, narrow
mounts/egress, partial snapshots, cleanup identity checks, graceful-stop failures,
archive traversal and isolated restore orchestration. Servarr independently passed
real disposable encrypted Restic backup/readback/retrieval and wrong-password
rejection. These do not prove deployed OpenSearch compatibility or application
restoration. Preserved REDs include `/tmp/wazuh-historical-drill-red.log` and
`/tmp/wazuh-cleanup-retry-red.log`, `/tmp/wazuh-restore-output-red.log` and
`/tmp/wazuh-restore-members-red.log`; the original plan is preserved at
`/tmp/wazuh-recovery-s07-first-draft.md`.

## Continuing gates and recovery

Publish reviewed leaves, pin the corrected transport, provision dedicated runtime
credentials, and validate the live manager hook/indexer API before the first
coordinated capture. Daily scheduling stays disabled until encrypted backup,
isolated restore and Wazuh-specific failed/overdue monitoring pass. Attach retention
only after reviewing affected index ages/counts and disposable-policy behavior.
Rollback disables new jobs and controller while preserving snapshots and enrollment
state. Removing retention cannot recover deleted indices; verified restoration is
the recovery path.

References: [existing owner recovery pattern](authentik-recovery.md),
[Wazuh native snapshots](https://documentation.wazuh.com/current/user-manual/wazuh-indexer/migrating-wazuh-indices.html),
[manager database backup](https://documentation.wazuh.com/current/migration-guide/creating/wazuh-central-components.html),
[OpenSearch permission boundaries](https://docs.opensearch.org/latest/security/access-control/permissions/).
