#!/usr/bin/env python3
"""Provision fixed reader logins in native Cognee and OpenBao; never grant datasets."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--apply', action='store_true')
parser.add_argument('--operator-secret-file', type=Path)
parser.add_argument('--pending-file', type=Path)
args = parser.parse_args()
domains = ('homelab', 'work')
paths = {domain: 'secret/data/lab/cognee/readers/' + domain for domain in domains}
token_file = args.operator_secret_file or Path.home() / '.vault-token'
if token_file.is_symlink() or token_file.stat().st_mode & 0o777 != 0o600:
    raise SystemExit('operator token file must be mode 0600')
if args.operator_secret_file:
    if not token_file.name.endswith('.secrets.json'):
        raise SystemExit('operator handoff must be a purpose-named *.secrets.json file')
    token = json.loads(token_file.read_text())['operatorToken']
else:
    token = token_file.read_text().strip()
if not isinstance(token, str) or not token or '\n' in token or '\r' in token:
    raise SystemExit('invalid operator token file')

def vault(path, payload=None):
    # curl configuration and secret payload travel only through SSH stdin.
    config = ['silent', 'show-error', 'url = ' + json.dumps('http://127.0.0.1:8200/v1/' + path),
              'header = ' + json.dumps('X-Vault-Token: ' + token),
              'write-out = "\\n%{http_code}"']
    if payload is not None:
        config += ['request = "POST"', 'header = "Content-Type: application/json"',
                   'data-binary = ' + json.dumps(json.dumps(payload))]
    result = subprocess.run(['ssh', '-p', '2222', '-o', 'BatchMode=yes', '192.168.10.210',
                             'curl --config -'], input='\n'.join(config) + '\n', text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise SystemExit('OpenBao SSH transport failed')
    body, status = result.stdout.rsplit('\n', 1)
    return int(status), json.loads(body) if body else None

status, capabilities = vault('sys/capabilities-self', {'paths': list(paths.values())})
if status != 200:
    raise SystemExit('operator token capabilities lookup failed: HTTP ' + str(status))
for path in paths.values():
    allowed = set(capabilities.get(path, []))
    if not ('root' in allowed or {'read', 'create', 'update'} <= allowed):
        raise SystemExit('operator token lacks reader-path read/create/update capability')

stored = {}
for domain, path in paths.items():
    status, response = vault(path)
    if status == 200:
        stored[domain] = response['data']['data']
    elif status != 404:
        raise SystemExit('reader credential lookup failed: HTTP ' + str(status))
if not args.apply:
    print(json.dumps({'capabilities': 'ready', 'stored_domains': sorted(stored), 'planned_domains': list(domains), 'grants': 'none'}))
    raise SystemExit(0)

handoff = args.pending_file or (token_file.parent if args.operator_secret_file else Path.cwd()) / 'cognee-reader-provision.secrets.json'
if not handoff.name.endswith('.secrets.json') or handoff.absolute() == token_file.absolute():
    raise SystemExit('pending reader handoff must be a separate *.secrets.json file')
if handoff.exists() and (handoff.is_symlink() or handoff.stat().st_mode & 0o077):
    raise SystemExit('unsafe provisioning handoff file')
pending = json.loads(handoff.read_text()) if handoff.exists() else {}

def preserve():
    fd, temporary = tempfile.mkstemp(dir=handoff.parent, prefix='.cognee-reader-provision.', suffix='.secrets.json')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(pending, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, handoff)
        directory = os.open(handoff.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def native(path, payload, form=False):
    if form:
        from urllib.parse import urlencode
        body, content_type = urlencode(payload), 'application/x-www-form-urlencoded'
    else:
        body, content_type = json.dumps(payload), 'application/json'
    result = subprocess.run(['kubectl', '--context', 'homelab', '-n', 'cognee', 'exec', '-i',
        'deployment/cognee', '--', 'curl', '--silent', '--show-error', '--write-out', '\n%{http_code}',
        '--header', 'Content-Type: ' + content_type, '--data-binary', '@-',
        'http://127.0.0.1:8000' + path], input=body, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise SystemExit('native Cognee transport failed; pending handoff retained')
    body, status = result.stdout.rsplit('\n', 1)
    return int(status), json.loads(body) if body else None

for domain in domains:
    email = 'svc-codex-cognee-' + domain + '-reader@homelab.pastelariadev.com'
    query = "select coalesce(json_agg(row_to_json(u)), '[]') from (select id::text,is_superuser,is_active from users where email='" + email + "') u"
    existing = json.loads(subprocess.check_output(['kubectl', '--context', 'homelab', '-n', 'cognee',
        'exec', 'postgres-0', '--', 'psql', '-At', '-U', 'cognee', '-d', 'cognee', '-c', query], text=True))
    credential = stored.get(domain) or pending.get(domain)
    if credential is None:
        if existing:
            raise SystemExit('native reader exists without a known Vault credential; refusing rotation')
        credential = {'username': email, 'password': secrets.token_urlsafe(36), 'domain': domain}
        pending[domain] = credential
        preserve()
    if credential.get('username') != email or credential.get('domain') != domain:
        raise SystemExit('stored reader identity does not match fixed scope')
    if not existing and credential.get('user_id'):
        raise SystemExit('recorded native reader disappeared; refusing replacement identity')
    if not existing:
        status, registered = native('/api/v1/auth/register', {'email': email, 'password': credential['password']})
        if status != 201 or registered.get('is_superuser') is not False:
            raise SystemExit('native reader registration failed; pending handoff retained')
        user_id = registered['id']
    else:
        if len(existing) != 1 or existing[0]['is_superuser'] or not existing[0]['is_active']:
            raise SystemExit('existing reader is not a unique active non-superuser')
        user_id = existing[0]['id']
    status, login = native('/api/v1/auth/login', {'username': email, 'password': credential['password']}, form=True)
    if status != 200 or not login.get('access_token'):
        raise SystemExit('reader credential does not authenticate; refusing rotation')
    if credential.get('user_id', user_id) != user_id:
        raise SystemExit('stored reader UUID changed')
    credential['user_id'] = user_id
    if domain not in stored:
        pending[domain] = credential
        preserve()
        status, _ = vault(paths[domain], {'options': {'cas': 0}, 'data': credential})
        if status not in (200, 204):
            raise SystemExit('reader Vault CAS create failed; pending handoff retained')
        status, reread = vault(paths[domain])
        if status != 200 or reread['data']['data'] != credential:
            raise SystemExit('reader Vault verification failed; pending handoff retained')
    pending.pop(domain, None)
    if pending:
        preserve()
    elif handoff.exists():
        handoff.unlink()
    print(json.dumps({'domain': domain, 'user_id': user_id, 'credential': 'verified in Vault', 'grants': 'unchanged'}))

if args.operator_secret_file:
    token_file.unlink()
