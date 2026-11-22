#!/usr/bin/env python3
"""Execute the embedded backup shell with disposable data and API fixtures."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'apps/cognee/resources.yaml'


def backup_script():
    block = MANIFEST.read_text().split('  backup.sh: |-\n', 1)[1].split('\n---\n', 1)[0]
    return textwrap.dedent(block)


class BackupTest(unittest.TestCase):
    def run_backup(self, *, fault='', replicas='2'):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        commands = root / 'bin'
        commands.mkdir()
        backups, source = root / 'backups', root / 'source'
        (source / 'data').mkdir(parents=True)
        (source / 'data/readiness.json.lock').touch()
        (source / 'data/document.txt').write_text('fixture')
        trace = root / 'trace'
        trace.touch()
        script = backup_script().replace('/backups/cognee', str(backups)).replace('/source-data', str(source))
        (root / 'backup.sh').write_text(script)
        kubectl = commands / 'kubectl'
        kubectl.write_text('#!' + shutil.which('python3') + '\n' + textwrap.dedent('''\
            import fcntl, os, pathlib, sys
            a = sys.argv[1:]
            fault, replicas = os.environ['FAULT'], os.environ['REPLICAS']
            def log(value):
                with open(os.environ['TRACE'], 'a') as f: f.write(value+'\\n')
            if 'get' in a:
                log('original-count')
                print(replicas)
            elif 'rollout' in a:
                log('readiness')
                if fault == 'readiness': sys.exit(44)
                assert '--timeout=300s' in a
            elif 'scale' in a:
                count = next(x.split('=',1)[1] for x in a if x.startswith('--replicas='))
                log('scale-'+count)
                if count != '0':
                    with open(os.environ['LOCK'], 'r+') as f:
                        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError: log('lock-held-during-restore'); sys.exit(49)
                    if fault in ('scale-back', 'archive-restore'): sys.exit(43)
                else:
                    files = list(pathlib.Path(os.environ['BACKUPS']).glob('*/replicas-before'))
                    log('durable-count='+ (files[0].read_text().strip() if files else 'missing'))
            elif 'wait' in a: log('quiesced')
            elif 'exec' in a:
                command = ' '.join(a)
                if 'drop table' in command or 'GRAPH.DELETE' in command: log('canary-cleanup')
                if 'pg_dump' in a: print('database fixture')
                elif 'cat /var/lib/falkordb/data/dump.rdb' in command: print('graph fixture')
                elif 'to_regclass' in command: print('f')
                elif 'encode(sha256' in command: print('identity fixture')
            elif 'apply' in a:
                sys.stdin.read(); log('success-marker')
            elif 'label' in a: print(sys.stdin.read())
            elif 'create' in a: print('fixture marker')
            else: sys.exit(99)
            '''))
        kubectl.chmod(0o755)
        for name, body in {
            'timeout': 'echo "timeout $*" >>"$TRACE"; exec '+shutil.which('timeout')+' "$@"',
            'tar': 'echo archive >>"$TRACE"; if [ "$FAULT" = archive ] || [ "$FAULT" = archive-restore ]; then exit 42; fi; if [ "$FAULT" = term ]; then kill -TERM "$PPID"; exit 143; fi; exec '+shutil.which('tar')+' "$@"',
            'find': 'echo retention >>"$TRACE"',
            'sync': 'echo durable-sync >>"$TRACE"; [ "$FAULT" != sync ] || exit 46; exec '+shutil.which('sync')+' "$@"',
        }.items():
            path = commands / name
            path.write_text('#!'+shutil.which('bash')+'\nset -eu\n'+body+'\n')
            path.chmod(0o755)
        env = os.environ | {'PATH': str(commands)+':'+os.environ['PATH'], 'FAULT': fault,
                            'REPLICAS': replicas, 'TRACE': str(trace), 'BACKUPS': str(backups),
                            'LOCK': str(source/'data/readiness.json.lock')}
        result = subprocess.run(['bash', str(root/'backup.sh')], env=env,
                                capture_output=True, text=True, timeout=10)
        return result, trace.read_text().splitlines(), backups

    def test_success_restores_original_count_and_readiness_before_marker(self):
        result, trace, backups = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        for before, after in [('durable-sync','scale-0'), ('archive','scale-2'),
                              ('scale-2','readiness'), ('readiness','success-marker'),
                              ('success-marker','retention')]:
            self.assertIn(before, trace)
            self.assertIn(after, trace)
            self.assertLess(trace.index(before), trace.index(after), trace)
        self.assertIn('durable-count=2', trace)
        self.assertEqual(trace.count('scale-2'), 1)
        timeouts = [x for x in trace if x.startswith('timeout ')]
        self.assertTrue(timeouts and all(x.startswith('timeout -k 5 ') for x in timeouts), timeouts)
        self.assertTrue(any(x.startswith('timeout -k 5 330 ') and 'rollout status' in x for x in timeouts))
        self.assertEqual(next(backups.glob('*/replicas-before')).read_text(), '2\n')

    def test_archive_failure_restores_but_preserves_failure(self):
        result, trace, _ = self.run_backup(fault='archive')
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual(trace.count('scale-2'), 1, trace)
        self.assertIn('readiness', trace)
        self.assertNotIn('success-marker', trace)
        self.assertNotIn('retention', trace)

    def test_restore_failures_never_publish_success_or_retry_cleanup(self):
        for fault in ('scale-back', 'readiness'):
            with self.subTest(fault=fault):
                result, trace, _ = self.run_backup(fault=fault)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(trace.count('scale-2'), 1, trace)
                self.assertNotIn('success-marker', trace)
                self.assertNotIn('retention', trace)

    def test_zero_or_invalid_initial_count_cannot_change_application(self):
        for replicas in ('0', '', '-1', 'two'):
            with self.subTest(replicas=replicas):
                result, trace, _ = self.run_backup(replicas=replicas)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(x.startswith('scale-') for x in trace), trace)
                self.assertNotIn('archive', trace)
                self.assertNotIn('success-marker', trace)

    def test_term_restores_without_success(self):
        result, trace, _ = self.run_backup(fault='term')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(trace.count('scale-2'), 1, trace)
        self.assertIn('readiness', trace)
        self.assertNotIn('success-marker', trace)
        self.assertNotIn('retention', trace)



    def test_original_failure_survives_failed_restoration(self):
        result, trace, _ = self.run_backup(fault='archive-restore')
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual(trace.count('scale-2'), 1, trace)
        self.assertNotIn('success-marker', trace)

    def test_failed_durability_barrier_never_quiesces(self):
        result, trace, _ = self.run_backup(fault='sync')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(x.startswith('scale-') for x in trace), trace)
        self.assertNotIn('success-marker', trace)


if __name__ == '__main__':
    unittest.main()
