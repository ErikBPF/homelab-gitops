import hashlib
from contextlib import closing
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('drill', ROOT / 'scripts/wazuh-restore-drill.py')
drill = importlib.util.module_from_spec(spec); spec.loader.exec_module(drill)


class IsolatedRestore(unittest.TestCase):
    def test_archive_member_limit_stops_reading_headers(self):
        source = MagicMock()
        count = 0
        def members():
            nonlocal count
            for _ in range(250002):
                count += 1
                yield tarfile.TarInfo('fixture')
        source.__iter__.side_effect = members
        source.getmembers.side_effect = lambda: list(members())
        context = MagicMock(); context.__enter__.return_value = source
        with tempfile.TemporaryDirectory() as directory, patch.object(drill.tarfile, 'open', return_value=context):
            with self.assertRaisesRegex(ValueError, 'restore budget'):
                drill.extract(Path(directory) / 'fixture.tar', Path(directory) / 'scratch')
        self.assertLessEqual(count, 250001)
        source.extractall.assert_not_called()

    def test_command_rejects_output_before_child_finishes(self):
        with self.assertRaisesRegex(ValueError, 'diagnostic output'):
            drill.command([sys.executable, '-c',
                'import os,time; os.write(1,b"x"*(1024*1024+1)); time.sleep(10)'], timeout=0.5)

    def test_command_deadline_and_input(self):
        self.assertEqual(drill.command([sys.executable, '-c',
            'import sys; print(sys.stdin.buffer.read().decode())'], data=b'fixture'), b'fixture\n')
        with self.assertRaises(subprocess.TimeoutExpired):
            drill.command([sys.executable, '-c', 'import time; time.sleep(10)'], timeout=0.1)

    def test_historical_verified_bundle_remains_restorable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hashes = {}
            for name in drill.ARCHIVES:
                (root / name).write_bytes(b'fixture')
                hashes[name] = hashlib.sha256(b'fixture').hexdigest()
            old = int(time.time()) - 172800
            manifest = dict(indexer_image=drill.IMAGE, authd_sha256=drill.PIN,
                indexer_state='SUCCESS', failed_shards=0,
                wazuh_databases_gracefully_stopped=True, managers_recovered=True,
                indexer_snapshot='wazuh-fixture', indices=['wazuh-alerts-fixture'],
                started=old, completed=old + 10, sha256=hashes)
            (root / 'capture.json').write_text(json.dumps(manifest))
            self.assertEqual(drill.validate_bundle(root)['started'], old)

    def test_archive_cannot_escape_scratch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); archive = root / 'evil.tar'
            with tarfile.open(archive, 'w') as output:
                entry = tarfile.TarInfo('../outside'); entry.size = 4
                output.addfile(entry, io.BytesIO(b'data'))
            with self.assertRaises(tarfile.TarError): drill.extract(archive, root / 'scratch')
            self.assertFalse((root / 'outside').exists())

    def test_actual_sqlite_integrity_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'wazuh/var/ossec/queue/db/global.db'
            db.parent.mkdir(parents=True)
            with closing(sqlite3.connect(db)) as connection: connection.execute('CREATE TABLE fixture (id INTEGER)')
            self.assertEqual(drill.manager_checks(root, False), 1)
            db.write_bytes(b'not a valid SQLite database')
            with self.assertRaises(sqlite3.DatabaseError): drill.manager_checks(root, False)

    def test_real_public_certificate_and_private_key_correspondence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); etc = root / 'wazuh/var/ossec/etc'; etc.mkdir(parents=True)
            db = root / 'wazuh/var/ossec/queue/db/global.db'; db.parent.mkdir(parents=True)
            with closing(sqlite3.connect(db)) as connection: connection.execute('CREATE TABLE fixture (id INTEGER)')
            drill.command(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=disposable',
                           '-keyout',str(etc/'sslmanager.key'),'-out',str(etc/'sslmanager.cert')])
            der = drill.command(['openssl','x509','-in',str(etc/'sslmanager.cert'),'-outform','DER'])
            with patch.object(drill,'PIN',hashlib.sha256(der).hexdigest().upper()):
                self.assertEqual(drill.manager_checks(root, True),1)
                drill.command(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',str(etc/'sslmanager.key')])
                with self.assertRaisesRegex(ValueError,'does not match'): drill.manager_checks(root, True)

    def test_container_has_no_network_or_production_identity_and_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repository = root/'repository'; repository.mkdir()
            manifest = {'indexer_snapshot':'wazuh-fixture','native_uuid':'fixture-id','indices':['wazuh-alerts-fixture']}
            calls = []
            def command(args, **kwargs):
                calls.append(args)
                if args[1] == 'run' or args[1] == 'rm': return b'fixture'
                url = next(arg for arg in args if arg.startswith('http:'))
                if '_cat/indices' in url: return b'[{"index":"wazuh-alerts-fixture","docs.count":"1"}]'
                if '_restore?' in url: return b'{"snapshot":{"shards":{"failed":0,"successful":1,"total":1}}}'
                if url.endswith('wazuh-fixture'): return b'{"snapshots":[{"uuid":"fixture-id"}]}'
                return b'{"acknowledged":true}'
            with patch.object(drill,'command',side_effect=command),patch.object(drill.os,'chown'):
                self.assertEqual(drill.indexer_restore(repository,root/'data',root/'config',manifest),1)
            start = calls[0]
            self.assertIn('--network=none',start)
            self.assertIn('--pull=never',start)
            self.assertIn('--cap-drop=ALL',start)
            self.assertFalse(any('/run/secrets' in item or '/var/run/docker.sock' in item for item in start))
            self.assertEqual(calls[-1][:3],['docker','rm','-f'])


if __name__ == '__main__': unittest.main()
