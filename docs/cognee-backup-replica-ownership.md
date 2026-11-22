# Cognee backup replica ownership

## Outcome and why

The November 22 scheduled backup failed with `tar: short read`. Its container
ran from 06:15:22 to 06:16:00 UTC. Cognee's replacement pod was created at
06:15:24 and running at 06:15:31, before backup completion. The existing backup
script scales the application to zero, waits for pod deletion, archives its
files and restores the original replica count in an EXIT trap. The rendered
Argo Application enables self-healing and had no replica-count exception.
That allows reconciliation to restart writers inside the intended quiet period.
The timing establishes a broken quiescence boundary; the archive error alone
does not exclude the historical host/storage faults.

Human instruction: “continue”, following the recovery, merge and deployment
sequence. This fix addresses the failed backup without altering its content,
credentials, destination, 14-day retention policy or workload image.

## Changes

Only `apps/Deployment` named `cognee` in namespace `cognee` delegates
`/spec/replicas` to the existing backup/operator path. `RespectIgnoreDifferences`
keeps that exception effective during sync as well as comparison. Other fields
and workloads retain Argo reconciliation; the global automated/self-heal policy
is unchanged. Initial deployment still receives the declared replica count.
This uses [Argo's existing mechanism](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/#respect-ignore-differences-configs).

The backup also records its validated positive original replica count in
`replicas-before`, restores that count and waits for readiness before publishing
success or pruning old archives. Failure paths preserve the original error and
report cleanup failure. The readiness lock is released before restoring the app;
TERM/INT/HUP exit through bounded cleanup, while hard kill and node loss remain
manual recovery cases. Namespace-scoped deployment read/watch permissions support
the existing kubectl rollout watcher, without another controller.

The existing Cognee contract checks the exact exception and sync option, so a
broader suppression or removal fails the owner test. The parent app rendering
was separately inspected to confirm it retains both the exception and self-heal.

## Evidence and limits

Before the production edit, `bash tests/cognee-contract.sh` failed with
“Cognee backup replica ownership must survive Argo reconciliation”. After the
edit, that contract, `tests/automated-sync-containment.sh`, rendered Application
assertions and `git diff --check` passed in the existing GitOps tools environment.
The contract includes the existing restore identity check. These are static
checks, not a real controller or archive replay. A local schema-check attempt
could not run because kubeconform was absent; required PR CI remains the schema
and full repository gate. Independent review found the ignored-restoration failure path. Before its fix,
execution tests of the embedded script produced nine assertion failures across
five tests. Afterward seven tests passed, covering success order, archive failure,
restoration failure, invalid/zero count, signal interruption, failed durable write
and combined capture/restoration failure. Bash syntax and ShellCheck passed.
Logs are `/tmp/cognee-backup-{red,green,contract}.log` on the executing workstation.
Final independent source review found no blockers and reran the owner contract
and seven script tests successfully. Live backup verification remains pending.

## Rollout and recovery

Merge after current-head CI/review; verify the root application has installed
the exact Cognee exception before creating a backup job. Run one bounded backup
through the owner `just cognee-backup` recipe. Observe that replicas stay zero
through archival, then return to their original value, the job completes and a
new success marker appears. A successful backup does not prove restoration.
Do not run a restore against production or retry repeatedly after a storage error.

The tradeoff is explicit: Argo will no longer correct manual or accidentally
stuck replica counts for this deployment. If backup cleanup fails, check that
no backup is active, read the numeric `replicas-before` file in that job's backup directory on the
existing backup volume, restore that recorded count with
`kubectl --context homelab -n cognee scale deployment/cognee --replicas=COUNT`,
then verify `kubectl --context homelab -n cognee rollout status deployment/cognee --timeout=300s`. Do not guess the count or restore an old run's value. Existing API/backup freshness alerts
remain necessary. A hard-killed job may not execute its EXIT trap. Removing this
exception before repairing backup coordination would reintroduce the race;
rollback should first prevent a backup from running concurrently with sync.


## Published review amendment: termination budget

The first published-head review found that the default 30-second pod grace
could kill the job before restoration, especially because Bash defers a signal
trap while a foreground capture command runs. This was not covered by the
original immediate-exit signal fixture. The repair gives the pod 600 seconds
of termination grace and bounds every post-quiesce capture command at 120
seconds plus five seconds kill grace. One checksum command covers all archive
members. Maximum foreground deferral (125 seconds) plus bounded cleanup
(440 seconds) fits that grace. The new deferred-child signal regression failed before the repair (the fixture
had to kill Bash) and now observes exit 143 after exactly one restoration,
without a success marker. Graph enumeration now propagates command failure;
its pre-fix fixture incorrectly exited zero. All nine script tests and the full
owner contract pass. Independent review found no remaining blockers.

The previous three successful scheduled jobs completed in approximately 57,
62 and 57 seconds; this is calibration evidence, not a guarantee for future
larger archives. A phase exceeding 120 seconds now fails and restores Cognee;
retune only with measured evidence. The Job execution deadline remains 900
seconds. At deadline, the owner's 15-minute watcher may return before up to
600 seconds of termination cleanup finishes. Do not start another backup or
perform manual restoration while any old backup pod is active or terminating.
SIGKILL, node loss or kernel/storage stalls still require the recorded-count
recovery procedure. A disposable pod using the exact pinned backup image on w-3 verified BusyBox's
`timeout -k` forced-kill behavior and `sync -f` on a file and directory, exiting
zero. It had no credentials, service-account token or persistent data volumes
and was deleted after completion. This tool check does not exercise a real
backup or NFS recovery. Live backup verification remains pending.
