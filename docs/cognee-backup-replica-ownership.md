# Cognee backup: quiescence and verified restoration

**Status:** source reviewed; live rollout and backup retry pending. Human seed:
“continue”, following the Kepler recovery and deployment sequence.

## Outcome and why

The November 22 scheduled backup failed with `tar: short read`. Its container
ran from 06:15:22 to 06:16:00 UTC, but Cognee's replacement pod was running at
06:15:31, inside the intended scale-to-zero quiet period. The rendered Argo
Application enabled self-healing without a replica exception. That could restart
writers while the existing backup archived their files. The timing establishes
broken quiescence; the archive error does not exclude historical storage faults.

## Changes and expected behavior

Only `apps/Deployment` named `cognee` in namespace `cognee` delegates
`/spec/replicas` to the backup/operator path. `RespectIgnoreDifferences` applies
the exception during sync as well as comparison. Other fields and applications
retain reconciliation; initial deployment still receives the declared count.
This follows [Argo's existing mechanism](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/#respect-ignore-differences-configs).

Before quiescence, the backup validates and durably records its positive original
count in `replicas-before`. It restores that count and verifies readiness before
publishing success or pruning archives. Capture, graph enumeration and cleanup
failures remain failures. Explicit unlocking releases the shared open-file
lock even if a surviving child inherited its descriptor; closing the parent's
copy alone is insufficient. Cleanup still attempts restoration if unlocking fails.
Namespace-scoped deployment list/watch reads support kubectl's standard watcher.

Post-quiesce foreground commands are bounded to 120 seconds plus five seconds
kill grace. Cleanup is bounded to 455 seconds; the combined 580 seconds fit the
pod's 600-second termination grace. EXIT and signal paths clean up once. The
Job execution deadline remains 900 seconds. The prior three successful jobs took
approximately 57, 62 and 57 seconds; future larger/slower phases exceeding the
new bound fail explicitly and need measured retuning. No destination, retention,
credential, image or production restore behavior is changed.

## Evidence and limits

Tests execute the real embedded script with disposable files and a simulated
Kubernetes API. Observed failures before fixes covered missing replica ownership,
restoration/false success, deferred TERM, ignored graph-list failure and inherited
lock descriptors. All ten script tests, the owner Cognee contract, automated-sync
containment, rendered policy assertions, Bash syntax and ShellCheck now pass.
Independent review reran the ten tests and found no blockers.

The first published CI passed, then hosted review identified inadequate default
termination grace. Amended CI exposed the inherited-descriptor race; the final
fixture reproduces it deterministically and preserves the deferred-TERM test.
Receipts: `/tmp/cognee-backup-*`, `/tmp/cognee-termination-*`,
`/tmp/cognee-graph-list-*` and `/tmp/cognee-inherited-lock-*` logs. Earlier drafts
remain in commits `304d1d9` and `18c5777`. Fresh current-head CI/review is required.

Two short-lived pods using the exact pinned helper image verified timeout kill
grace, file/directory sync flags and explicit unlocking while a child retained
the descriptor. They had no credentials, service-account token or persistent
volumes and were deleted. This proves tool compatibility, not NFS recovery or
backup consistency. A successful backup also would not prove restoration.

## Rollout, risks and recovery

After merge, verify both the live Application exception and backup script/RBAC,
then run one bounded owner `just cognee-backup`. Observe quiescence, completion,
restored replicas/readiness and a new success marker. Keep the failed job receipt.

Argo no longer repairs this one replica count. SIGKILL, node loss or kernel/storage
stalls can still require manual recovery. At the execution deadline, cleanup may
continue after the owner's 15-minute watcher returns. Never start another backup
or restore replicas while an old backup pod is active or terminating.

Once inactive, read that job's `replicas-before` from its backup directory; do not
guess or use another run's value. Restore it with `kubectl --context homelab -n
cognee scale deployment/cognee --replicas=COUNT`, then verify rollout with a
300-second timeout and application availability. Existing availability/freshness
alerts remain necessary. Before rolling back the Argo exception, prevent backup
and sync overlap; otherwise rollback reintroduces the race.
