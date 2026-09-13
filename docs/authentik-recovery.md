# Authentik recovery drill

Retrieve a completed Authentik bundle from the encrypted offsite Restic repository
into a private local directory. The bundle contains `postgres.dump`, `created-at`
(decimal UTC epoch at dump start), and `SHA256SUMS` covering those files in that
order. Treat the dump as sensitive identity data. Do not paste its contents into
logs or chat.

Run `just restore-drill-authentik /private/path/to/bundle`. The script
rejects incomplete, symlinked, corrupt, future-dated, or older-than-24-hour bundles
before accessing Kubernetes. It targets the existing `homelab` context and
`authentik` namespace.

The drill creates a uniquely named deny-all NetworkPolicy before its scratch Pod.
The Pod uses temporary database storage, no service-account token, and no exposed
Service. PostgreSQL 17.10 accepts connections only over Pod loopback; Authentik
2026.5.6 runs a one-shot application check, not a server or worker. The only
production secret referenced is `AUTHENTIK_SECRET_KEY`; production database
credentials and endpoints are not inherited.

After `pg_restore --exit-on-error`, the application checks migration state and
expected configuration relationships. A temporary user's correct password must
validate and an incorrect password must fail, inside a rolled-back transaction.
Only pass/fail metadata and image identifiers are printed. Database and application
output is suppressed. Cleanup removes only the drill's own Pod and policy. If Pod
cleanup fails, the policy remains and the reported resource name identifies what
requires operator cleanup; the original failure status is retained.

This proves isolated database restoration and application password handling. It
does not prove browser login, MFA/OIDC journeys, or cold OpenBao recovery: the drill
uses the currently available runtime secret key. The 24-hour loss target also
requires ongoing successful offsite publication; one successful drill does not
prove future schedule delivery.

Current application `/data`, `/media` and `/certs` paths were empty and unmounted
during the September 13 checks; `/custom-templates` and `/blueprints/custom` were
absent on both server and worker. Confirm filesystem uploads, custom templates, certificates,
and custom blueprints remain absent or separately protected before claiming full
application recovery. Adding such data requires extending backup coverage.

Local orchestration checks (no cluster access):

```sh
python3 -m unittest discover -s tests -p test_authentik_restore_drill.py -v
```

These tests exercise isolation manifests, checksum rejection, restoration failure,
application failure, and cleanup. Actual restore acceptance still requires an
offsite-retrieved bundle and a successful isolated run.

## Producer and deployment

`just backup-authentik` invokes the deployed CronJob once. Scheduled dumps start
at 00:00 and 12:00 UTC, with a five-minute start deadline and ten-minute execution
limit. The producer connects directly to PostgreSQL without Kubernetes API
credentials, publishes checksummed bundles atomically, and retains 14 days of
completed staging under `/fast/k8s/authentik-backups`. The NFS-backed PV is Retain
and reserved for the Authentik backup claim; platform Argo permission includes
PersistentVolume so it can reconcile this declared volume.

Prepare the owner-only directory through Desktop before reconciling the chart.
The existing Servarr Restic transport runs locally at 00:20/12:20 UTC and offsite
at 00:40/12:40 UTC. It rejects bundles two hours old or older. Grafana warns at
18 hours of offsite source age and marks the accepted target breached at 24 hours;
missing evidence also alerts. It measures dump start time, not upload time.

Deployment order: reviewed Desktop substrate deployment, reviewed GitOps project
and Authentik reconciliation, initial manual dump, reviewed Servarr transport
reconciliation and initial local/offsite runs, then GitOps monitoring reconciliation.
Use each owner's published revision and documented deployment recipes. Verify
fresh offsite evidence before considering this recovery slice deployed.

For offsite acceptance, run Servarr's `just kepler-authentik-offsite-restore
NEW_PRIVATE_DIRECTORY`, then pass the returned bundle directory to the restore
recipe here. This is an operator handoff of backup data, not a runtime dependency
on another working tree. The receiver returns the exact snapshot ID and source
timestamp; preserve those metadata with the drill verdict. Remove the private
local bundle after the drill.

Rollback through the same owner deployment paths: suspend/remove the producer and
transport schedules and remove new monitoring rules as needed. Retain the backup
volume, staged data and encrypted snapshots; never roll back the live Authentik
database as part of reversing this backup slice.

## Local implementation receipt

September 13, 01:23:36 UTC: a real PostgreSQL dump from the current database
produced a 7,184,951-byte custom archive. At 01:25:48 UTC, the isolated PostgreSQL
17.10/AuthentiK 2026.5.6 application drill passed. Scratch Pod, deny policy and
private local archive were removed. This pre-publication check used a direct
read-only database export and the live runtime key; it is not evidence of the
scheduled producer, offsite transport or independent OpenBao bootstrap recovery.
