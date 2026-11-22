#!/usr/bin/env python3
"""Execute the embedded backup shell with disposable data and API fixtures."""
import os
from pathlib import Path
import shutil
import signal
import time
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
                if 'GRAPH.LIST' in a and fault == 'graph-list': sys.exit(47)
                if 'pg_dump' in a:
                    if fault == 'inherited-lock':
                        os.fstat(9)
                        child = os.fork()
                        if child == 0:
                            os.setsid()
                            with open(os.devnull, 'wb') as output:
                                os.dup2(output.fileno(), 1)
                                os.dup2(output.fileno(), 2)
                            os.execlp('sleep', 'sleep', '30')
                        pathlib.Path(os.environ['HOLDER_PID']).write_text(str(child))
                        log('inherited-lock-holder')
                    print('database fixture')
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
            'timeout': 'echo "timeout $*" >>"$TRACE"; if [ "$FAULT" = deferred-term ]; then exec '+shutil.which('timeout')+' -k 0.1 0.8 "${@:4}"; fi; exec '+shutil.which('timeout')+' "$@"',
            'tar': 'echo archive >>"$TRACE"; if [ "$FAULT" = deferred-term ]; then trap \"\" TERM; echo archive-blocked >>"$TRACE"; while :; do sleep 1; done; fi; if [ "$FAULT" = archive ] || [ "$FAULT" = archive-restore ]; then exit 42; fi; if [ "$FAULT" = term ]; then kill -TERM "$PPID"; exit 143; fi; exec '+shutil.which('tar')+' "$@"',
            'find': 'echo retention >>"$TRACE"',
            'sync': 'echo durable-sync >>"$TRACE"; [ "$FAULT" != sync ] || exit 46; exec '+shutil.which('sync')+' "$@"',
        }.items():
            path = commands / name
            path.write_text('#!'+shutil.which('bash')+'\nset -eu\n'+body+'\n')
            path.chmod(0o755)
        holder_pid = root / 'holder-pid'
        def stop_holder():
            if holder_pid.exists():
                try:
                    os.killpg(int(holder_pid.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 2
                state_file = Path('/proc') / holder_pid.read_text() / 'stat'
                while state_file.exists() and time.monotonic() < deadline:
                    try:
                        if state_file.read_text().split()[2] == 'Z':
                            break  # Reparented child exited; no descriptors remain open.
                    except FileNotFoundError:
                        break
                    time.sleep(0.01)
                else:
                    self.assertFalse(state_file.exists(), 'lock fixture child did not exit')
        self.addCleanup(stop_holder)
        env = os.environ | {'HOLDER_PID': str(holder_pid), 'PATH': str(commands)+':'+os.environ['PATH'], 'FAULT': fault,
                            'REPLICAS': replicas, 'TRACE': str(trace), 'BACKUPS': str(backups),
                            'LOCK': str(source/'data/readiness.json.lock')}
        try:
            if fault == 'deferred-term':
                process = subprocess.Popen(['bash', str(root/'backup.sh')], env=env,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, start_new_session=True)
                try:
                    deadline = time.monotonic() + 5
                    while 'archive-blocked' not in trace.read_text() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertIn('archive-blocked', trace.read_text())
                    # Kubelet sends TERM to PID 1, not directly to its foreground tar.
                    process.send_signal(signal.SIGTERM)
                    try:
                        stdout, stderr = process.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        stdout, stderr = process.communicate()
                    result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            else:
                result = subprocess.run(['bash', str(root/'backup.sh')], env=env,
                                        capture_output=True, text=True, timeout=10)
        finally:
            stop_holder()
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
        checksum_files = {line.split()[-1] for line in next(backups.glob('*/SHA256SUMS')).read_text().splitlines()}
        self.assertEqual(checksum_files, {'postgres.dump', 'falkordb.rdb', 'postgres-counts.tsv',
                                        'falkor-counts.tsv', 'cognee-data.tar', 'identity-digest.sha256'})

    def test_archive_failure_restores_but_preserves_failure(self):
        result, trace, _ = self.run_backup(fault='archive')
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual(trace.count('scale-2'), 1, trace)
        self.assertIn('readiness', trace)
        self.assertNotIn('success-marker', trace)
        self.assertNotIn('retention', trace)

    def test_graph_enumeration_failure_restores_without_success(self):
        result, trace, _ = self.run_backup(fault='graph-list')
        self.assertEqual(result.returncode, 47, result.stderr)
        self.assertEqual(trace.count('scale-2'), 1, trace)
        self.assertIn('readiness', trace)
        self.assertNotIn('archive', trace)
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



    def test_term_during_blocked_foreground_capture_restores_before_grace(self):
        result, trace, _ = self.run_backup(fault='deferred-term')
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertEqual(trace.count('scale-2'), 1, trace)
        self.assertIn('readiness', trace)
        self.assertNotIn('success-marker', trace)
        self.assertNotIn('retention', trace)

    def test_inherited_lock_descriptor_cannot_block_restoration(self):
        result, trace, _ = self.run_backup(fault='inherited-lock')
        self.assertIn('inherited-lock-holder', trace)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(trace.count('scale-2'), 1, trace)
        self.assertNotIn('lock-held-during-restore', trace)
        self.assertIn('readiness', trace)
        self.assertIn('success-marker', trace)

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
