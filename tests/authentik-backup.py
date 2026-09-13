#!/usr/bin/env python3
"""Exercise atomic publication and rejection of failed PostgreSQL dumps."""
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'platform/authentik/files/backup.sh'


class BackupTest(unittest.TestCase):
    def test_atomic_complete_bundle_and_failure_cleanup(self):
        self.assertTrue(SCRIPT.is_file(), 'Authentik dump producer is missing')
        for failed in ('', 'pg_dump', 'pg_restore'):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                staging = root / 'backups'
                staging.mkdir()
                for command in ('pg_dump', 'pg_restore', 'pg_isready'):
                    fake = root / command
                    fake.write_text(f'#!{shutil.which("bash")}\nset -eu\n'
                                    '[[ ${FAIL_COMMAND:-} != ' + command + ' ]] || exit 7\n'
                                    + ('for arg; do case $arg in --file=*) printf fixture > "${arg#--file=}";; esac; done\n'
                                       if command == 'pg_dump' else 'exit 0\n'))
                    fake.chmod(0o755)
                result = subprocess.run(['bash', str(SCRIPT), str(staging)],
                                        env=os.environ | {'PATH': f'{root}:{os.environ["PATH"]}',
                                                          'FAIL_COMMAND': failed},
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, not failed, result.stderr)
                bundles = list(staging.iterdir())
                if failed:
                    self.assertEqual(bundles, [])
                else:
                    self.assertEqual(len(bundles), 1)
                    bundle = bundles[0]
                    self.assertRegex(bundle.name, r'^\d{8}T\d{6}Z$')
                    self.assertEqual({p.name for p in bundle.iterdir()},
                                     {'postgres.dump', 'created-at', 'SHA256SUMS'})
                    self.assertTrue((bundle / 'created-at').read_text().strip().isdigit())
                    self.assertEqual((bundle / 'postgres.dump').stat().st_mode & 0o777, 0o640)
                    subprocess.run(['sha256sum', '--check', 'SHA256SUMS'], cwd=bundle,
                                   check=True, capture_output=True)

    def test_readiness_before_snapshot_and_timeout(self):
        for ready in (True, False):
            with self.subTest(ready=ready), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                staging = root / 'backups'
                staging.mkdir()
                commands = {
                    'pg_isready': 'echo probe >> "$TRACE"\n'
                        '[[ $READY == yes ]] || exit 2\n'
                        '[[ $(wc -l < "$TRACE") -ge 3 ]] || exit 2\n'
                        'touch "$READY_FILE"\n',
                    'pg_dump': 'echo dump >> "$TRACE"\n'
                        'test -e "$READY_FILE" || exit 9\n'
                        'for arg; do case $arg in --file=*) printf fixture > "${arg#--file=}";; esac; done\n',
                    'pg_restore': 'exit 0\n',
                    'date': 'test -e "$READY_FILE" || exit 10\n'
                        + 'exec ' + shutil.which('date') + ' "$@"\n',
                }
                for name, body in commands.items():
                    command = root / name
                    command.write_text('#!' + shutil.which('bash') + '\nset -eu\n' + body)
                    command.chmod(0o755)
                trace = root / 'trace'
                result = subprocess.run(['bash', str(SCRIPT), str(staging)],
                    env=os.environ | {'PATH': f'{root}:{os.environ["PATH"]}',
                        'TRACE': str(trace), 'READY_FILE': str(root / 'ready'),
                        'READY': 'yes' if ready else 'no'},
                    capture_output=True, text=True, timeout=40)
                calls = trace.read_text().splitlines() if trace.exists() else []
                if ready:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(calls, ['probe', 'probe', 'probe', 'dump'])
                    self.assertEqual(len(list(staging.iterdir())), 1)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('PostgreSQL readiness timed out after 30 seconds', result.stderr)
                    self.assertGreaterEqual(calls.count('probe'), 2)
                    self.assertNotIn('dump', calls)
                    self.assertEqual(list(staging.iterdir()), [])

    def test_rendered_scheduled_backup_boundaries(self):
        result = subprocess.run(['helm', 'template', 'authentik', 'platform/authentik',
                                 '--namespace', 'authentik'], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        import yaml
        manifests = list(yaml.safe_load_all(result.stdout))
        jobs = [m for m in manifests if m and m.get('kind') == 'CronJob'
                and m['metadata']['name'] == 'authentik-backup']
        self.assertEqual(len(jobs), 1, 'Authentik scheduled producer is missing')
        spec = jobs[0]['spec']
        self.assertEqual(spec['schedule'], '0 0,12 * * *')
        self.assertEqual(spec['timeZone'], 'Etc/UTC')
        self.assertEqual(spec['concurrencyPolicy'], 'Forbid')
        self.assertEqual(spec['startingDeadlineSeconds'], 300)
        self.assertEqual(spec['jobTemplate']['spec']['activeDeadlineSeconds'], 600)
        pod = spec['jobTemplate']['spec']['template']['spec']
        self.assertFalse(pod['automountServiceAccountToken'])
        self.assertEqual(pod['securityContext']['runAsUser'], 1000)
        self.assertEqual(pod['securityContext']['runAsGroup'], 100)
        self.assertNotIn('fsGroup', pod['securityContext'], 'Owner UID already grants access; do not widen NFS staging modes')
        self.assertNotIn('serviceAccountName', pod)
        self.assertNotIn('app.kubernetes.io/instance',
                         spec['jobTemplate']['spec']['template']['metadata']['labels'])
        self.assertIn('@sha256:', pod['containers'][0]['image'])


if __name__ == '__main__':
    unittest.main()
