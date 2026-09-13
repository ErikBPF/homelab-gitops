"""Offline reader provisioning checks: no cluster, SSH, token store or real handoff."""
import contextlib
import ast
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/provision-cognee-readers.py'


class ProvisionTests(unittest.TestCase):
    def test_interrupted_handoff_replacement_preserves_old_credential(self):
        tree = ast.parse(SCRIPT.read_text())
        preserve = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'preserve')
        with tempfile.TemporaryDirectory() as directory:
            handoff = Path(directory) / 'handoff.secrets.json'
            handoff.write_text('{"homelab":{"password":"old-synthetic"}}')
            handoff.chmod(0o600)
            namespace = {'os': os, 'json': json, 'tempfile': tempfile, 'handoff': handoff,
                         'pending': {'homelab': {'password': 'new-synthetic'}}}
            exec(compile(ast.Module(body=[preserve], type_ignores=[]), str(SCRIPT), 'exec'), namespace)
            with patch('os.replace', side_effect=OSError('synthetic interruption')):
                with self.assertRaises(OSError):
                    namespace['preserve']()
            self.assertEqual(json.loads(handoff.read_text()), {'homelab': {'password': 'old-synthetic'}})
            self.assertEqual(list(Path(directory).iterdir()), [handoff])

    def run_helper(self, *, capabilities=200, existing=None, vault_existing=None, operator_handoff=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.vault-token').write_text('synthetic-operator-token')
            (root / '.vault-token').chmod(0o600)
            operator = root / 'cognee-reader-operator.secrets.json'
            if operator_handoff:
                operator.write_text(json.dumps({'operatorToken': 'synthetic-operator-token'}))
                operator.chmod(0o600)
            real_path = type(root)

            class LocalPath:
                @classmethod
                def home(cls):
                    return root

                @classmethod
                def cwd(cls):
                    return root

                def __new__(cls, *parts):
                    if parts == ('/home/erik/Documents/erik/homelab/cognee-reader-provision.secrets.json',):
                        parts = (root / 'cognee-reader-provision.secrets.json',)
                    return real_path(*parts)

            vault_data, users, calls = dict(vault_existing or {}), dict(existing or {}), []

            def run(command, *, input, **kwargs):
                calls.append(command)
                if command[0] == 'ssh':
                    config = dict(line.split(' = ', 1) for line in input.splitlines() if ' = ' in line)
                    url = json.loads(config['url'])
                    path = url.split('/v1/', 1)[1]
                    payload = json.loads(json.loads(config['data-binary'])) if 'data-binary' in config else None
                    if path == 'sys/capabilities-self':
                        body, status = {p: ['read', 'create', 'update'] for p in payload['paths']}, capabilities
                    elif payload is not None:
                        self.assertTrue(path.startswith('secret/data/lab/cognee/readers/'))
                        self.assertEqual(payload['options'], {'cas': 0})
                        self.assertNotIn(path, vault_data)
                        self.assertEqual((root / 'cognee-reader-provision.secrets.json').stat().st_mode & 0o777, 0o600)
                        vault_data[path] = payload['data'].copy()
                        body, status = {'data': {'version': 1}}, 200
                    elif path in vault_data:
                        body, status = {'data': {'data': vault_data[path]}}, 200
                    else:
                        body, status = {}, 404
                elif command[-1].endswith('/auth/register'):
                    payload = json.loads(input)
                    email = payload['email']
                    self.assertNotIn(email, users)
                    user_id = '00000000-0000-0000-0000-' + str(len(users) + 1).zfill(12)
                    users[email] = {'id': user_id, 'is_superuser': False, 'is_active': True}
                    body, status = {'id': user_id, 'is_superuser': False}, 201
                else:
                    self.assertTrue(command[-1].endswith('/auth/login'))
                    body, status = {'access_token': 'synthetic-login-secret'}, 200
                return subprocess.CompletedProcess(command, 0, json.dumps(body) + '\n' + str(status), '')

            def query(command, **kwargs):
                email = command[-1].split("where email='")[1].split("'")[0]
                return json.dumps([users[email]] if email in users else [])

            output = io.StringIO()
            failure = None
            argv = [str(SCRIPT), '--apply']
            if operator_handoff:
                argv += ['--operator-secret-file', str(operator)]
            with patch('pathlib.Path', LocalPath), patch('subprocess.run', run), patch('subprocess.check_output', query), patch.object(sys, 'argv', argv), contextlib.redirect_stdout(output):
                try:
                    runpy.run_path(str(SCRIPT), run_name='__main__')
                except SystemExit as error:
                    failure = str(error)
            if operator_handoff:
                self.assertEqual(operator.exists(), failure is not None)
            self.assertNotIn('synthetic-operator-token', output.getvalue())
            self.assertNotIn('synthetic-login-secret', output.getvalue())
            for value in vault_data.values():
                self.assertNotIn(value['password'], output.getvalue())
            self.assertFalse(any('/permissions/' in str(command) for command in calls))
            return failure, vault_data, users, calls, (root / 'cognee-reader-provision.secrets.json').exists()

    def test_create_store_verify_and_remove_handoff_without_grants(self):
        failure, stored, users, _, handoff = self.run_helper()
        self.assertIsNone(failure)
        self.assertEqual(len(stored), 2)
        self.assertEqual(len(users), 2)
        self.assertFalse(handoff)

    def test_capability_failure_performs_no_identity_or_secret_writes(self):
        failure, stored, users, calls, handoff = self.run_helper(capabilities=403)
        self.assertIn('HTTP 403', failure)
        self.assertEqual((stored, users), ({}, {}))
        self.assertEqual(len(calls), 1)
        self.assertFalse(handoff)

    def test_operator_handoff_is_retained_after_capability_denial(self):
        failure, stored, users, calls, handoff = self.run_helper(capabilities=403, operator_handoff=True)
        self.assertIn('HTTP 403', failure)
        self.assertEqual((stored, users), ({}, {}))
        self.assertEqual(len(calls), 1)
        self.assertFalse(handoff)

    def test_operator_handoff_is_consumed_after_verified_storage(self):
        failure, stored, users, _, handoff = self.run_helper(operator_handoff=True)
        self.assertIsNone(failure)
        self.assertEqual(len(stored), 2)
        self.assertEqual(len(users), 2)
        self.assertFalse(handoff)

    def test_existing_identity_without_credential_is_never_rotated(self):
        email = 'svc-codex-cognee-homelab-reader@homelab.pastelariadev.com'
        failure, stored, users, calls, handoff = self.run_helper(existing={email: {'id': 'existing', 'is_superuser': False, 'is_active': True}})
        self.assertIn('refusing rotation', failure)
        self.assertEqual(stored, {})
        self.assertEqual(len(users), 1)
        self.assertFalse(any(command[0] == 'kubectl' for command in calls))
        self.assertFalse(handoff)

    def test_existing_vault_credentials_reuse_fixed_native_identities(self):
        users, stored = {}, {}
        for domain in ('homelab', 'work'):
            email = 'svc-codex-cognee-' + domain + '-reader@homelab.pastelariadev.com'
            users[email] = {'id': domain, 'is_superuser': False, 'is_active': True}
            stored['secret/data/lab/cognee/readers/' + domain] = {
                'username': email, 'password': 'synthetic-' + domain,
                'domain': domain, 'user_id': domain}
        failure, result, identities, calls, handoff = self.run_helper(existing=users, vault_existing=stored)
        self.assertIsNone(failure)
        self.assertEqual(result, stored)
        self.assertEqual(identities, users)
        self.assertFalse(any('/auth/register' in str(command) for command in calls))
        self.assertFalse(handoff)


if __name__ == '__main__':
    unittest.main()
