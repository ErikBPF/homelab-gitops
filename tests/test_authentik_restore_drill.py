"""Exercise restore orchestration without contacting Kubernetes or reading real backups."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/authentik-restore-drill.sh'

class RestoreDrill(unittest.TestCase):
    def run_case(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / 'bundle'
            bundle.mkdir()
            (bundle / 'postgres.dump').write_bytes(b'PGDMPsynthetic')
            (bundle / 'created-at').write_text(str(int(datetime.now(timezone.utc).timestamp())) + '\n')
            (bundle / 'SHA256SUMS').write_text(''.join(hashlib.sha256((bundle / f).read_bytes()).hexdigest() + '  ' + f + '\n' for f in ['postgres.dump', 'created-at']))
            if mode == 'corrupt':
                (bundle / 'postgres.dump').write_bytes(b'bad')
            binary = root / 'bin'
            binary.mkdir()
            kubectl = binary / 'kubectl'
            kubectl.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
record={'args':args}
if 'create' in args:
    record['manifest']=json.load(sys.stdin)
if 'pg_restore' in args:
    record['input_size']=len(sys.stdin.buffer.read())
with Path(os.environ['CALLS']).open('a') as f:f.write(json.dumps(record)+'\\n')
mode=os.environ['MODE']
if mode=='restore-fail' and 'pg_restore' in args:
    print('synthetic-sensitive-db-error',file=sys.stderr);sys.exit(17)
if mode=='app-fail' and 'shell' in args:
    print('synthetic-sensitive-app-error',file=sys.stderr);sys.exit(19)
''')
            kubectl.chmod(0o755)
            env=dict(os.environ, PATH=f'{binary}:{os.environ["PATH"]}', CALLS=str(root/'calls'), MODE=mode)
            result=subprocess.run(['bash', str(SCRIPT), str(bundle)], env=env, capture_output=True, text=True, timeout=20)
            calls=[json.loads(x) for x in (root/'calls').read_text().splitlines()] if (root/'calls').exists() else []
            return result,calls

    def test_rejects_corruption_before_cluster_access(self):
        result,calls=self.run_case('corrupt')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('bundle',result.stderr)
        self.assertEqual(calls,[])

    def test_isolated_restore_and_application_check(self):
        result,calls=self.run_case('success')
        self.assertEqual(result.returncode,0,result.stderr)
        created=[c['manifest'] for c in calls if 'manifest' in c]
        self.assertEqual([m['kind'] for m in created],['NetworkPolicy','Pod'])
        policy,pod=created
        self.assertEqual(policy['spec']['policyTypes'],['Ingress','Egress'])
        self.assertEqual(policy['spec'].get('ingress',[]),[])
        self.assertEqual(policy['spec'].get('egress',[]),[])
        self.assertFalse(pod['spec']['automountServiceAccountToken'])
        self.assertTrue(all('emptyDir' in v for v in pod['spec']['volumes']))
        self.assertNotIn('hostNetwork',pod['spec'])
        auth=next(c for c in pod['spec']['containers'] if c['name']=='authentik')
        refs=[e for e in auth['env'] if 'valueFrom' in e]
        self.assertEqual([e['name'] for e in refs],['AUTHENTIK_SECRET_KEY'])
        self.assertNotIn('envFrom',auth)
        self.assertEqual(next(e['value'] for e in auth['env'] if e['name']=='AUTHENTIK_POSTGRESQL__HOST'),'127.0.0.1')
        restores=[c for c in calls if 'pg_restore' in c['args']]
        self.assertEqual(len(restores),1)
        self.assertIn('--exit-on-error',restores[0]['args'])
        app=next(c for c in calls if 'shell' in c['args'])
        self.assertIn('check_password', ' '.join(app['args']))
        self.assertIn('set_rollback', ' '.join(app['args']))
        deleted=[c for c in calls if 'delete' in c['args']]
        self.assertEqual(len(deleted),2)
        self.assertIn('passed',result.stdout)

    def test_failures_are_sanitized_and_cleanup_runs(self):
        for mode in ['restore-fail','app-fail']:
            with self.subTest(mode=mode):
                result,calls=self.run_case(mode)
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('synthetic-sensitive',result.stdout+result.stderr)
                self.assertNotIn('passed',result.stdout)
                self.assertEqual(len([c for c in calls if 'delete' in c['args']]),2)
                if mode=='restore-fail':
                    self.assertFalse(any('shell' in c['args'] for c in calls))

if __name__ == '__main__': unittest.main()
