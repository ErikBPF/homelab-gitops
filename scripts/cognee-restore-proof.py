#!/usr/bin/env python3
"""Prove restored files, scoped retrieval and denied reader writes without source output."""
import json
import os
from pathlib import Path
import subprocess
import sys

context, namespace = sys.argv[1:]
credentials_path = Path(os.environ["COGNEE_RESTORE_READER_CREDENTIAL_FILE"])
if credentials_path.stat().st_mode & 0o077:
    raise SystemExit("restore reader credential file must be mode 0600")
credentials = json.loads(credentials_path.read_text())
if set(credentials) != {"homelab", "work"}:
    raise SystemExit("both homelab and work reader credentials required")
kube = ["kubectl", "--context", context, "-n", namespace, "exec"]
query = """select coalesce(json_agg(row_to_json(r)), '[]') from
(select d.id::text dataset_id, d.name dataset_name, d.owner_id::text owner_id, u.email owner, x.raw_data_location,
x.original_data_location, x.content_hash, x.raw_content_hash
from datasets d join users u on u.id=d.owner_id
join dataset_data dd on dd.dataset_id=d.id join data x on x.id=dd.data_id) r"""
rows = json.loads(subprocess.check_output(kube + ["cognee-restore-postgres", "--", "psql", "-At", "-h", "127.0.0.1", "-U", "restore", "-d", "restore", "-c", query], text=True))
grant_query = "select coalesce(json_agg(row_to_json(r)), '[]') from (select u.email, a.dataset_id::text dataset_id from acls a join users u on u.id=a.principal_id join permissions p on p.id=a.permission_id where p.name='read') r"
grants = json.loads(subprocess.check_output(kube + ["cognee-restore-postgres", "--", "psql", "-At", "-h", "127.0.0.1", "-U", "restore", "-d", "restore", "-c", grant_query], text=True))
if not rows:
    raise SystemExit("restored database has no source documents")
# Input travels over stdin; passwords never enter argv, files or command output.
probe = r'''
import hashlib, json, os, pathlib, urllib.request, urllib.parse, urllib.error
rows, credentials, grants = INPUT
root = pathlib.Path('/var/lib/cognee/data').resolve()
readiness = json.loads((root / 'readiness.json').read_text())
assert readiness['version'] == 1
states = readiness['datasets']
queries = {}

def source_path(location):
    parsed = urllib.parse.urlsplit(location)
    if parsed.scheme not in ('', 'file') or parsed.netloc not in ('', 'localhost'):
        raise ValueError('restored data is not a local managed file')
    path = pathlib.Path(urllib.parse.unquote(parsed.path)).resolve()
    if not path.is_relative_to(root):
        raise ValueError('restored data escapes the managed data root')
    return path

for row in rows:
    state = states[row['dataset_id']]
    assert state['status'] == 'ready' and not state.get('pending_deletions')
    for column in ('raw_data_location', 'original_data_location'):
        if row[column]:
            path = source_path(row[column])
            assert path.is_relative_to(root) and path.is_file(), 'missing restored document'
    path = source_path(row['raw_data_location'])
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    assert digest in state['source_hashes'], 'restored source missing from readiness'
    assert hashlib.md5(data).hexdigest() == row['raw_content_hash'], 'raw content hash mismatch'
    original = source_path(row['original_data_location']).read_bytes()
    assert hashlib.md5(original).hexdigest() == row['content_hash'], 'original content hash mismatch'
    queries.setdefault(row['dataset_id'], []).append(data.decode('utf-8')[:160])

def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)

def request(path, body=None, token=None, method=None, form=False):
    headers = {}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if body is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        body = (urllib.parse.urlencode(body) if form else json.dumps(body)).encode()
    req = urllib.request.Request('http://127.0.0.1:8000' + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        return error.code, json.loads(raw) if raw else None

def assert_denied(status, result, allowed=(403, 409)):
    assert status in allowed, 'reader retrieval was not denied by the guard'
    details = {403: 'reader scope denied', 409: 'reader context changed or exceeded limit'}
    assert result == {'detail': details[status]}, 'denied reader response contains unexpected context'

embedding_request = urllib.request.Request(os.environ['EMBEDDING_ENDPOINT'].rstrip('/') + '/embeddings',
    data=json.dumps({'model': os.environ['EMBEDDING_MODEL'].removeprefix('openai/'), 'input': 'isolated restore dimension check'}).encode(),
    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + os.environ['EMBEDDING_API_KEY']})
with urllib.request.urlopen(embedding_request, timeout=180) as response:
    assert len(json.load(response)['data'][0]['embedding']) == 1024, 'embedding dimension mismatch'
assert request('/api/v1/datasets')[0] == 401
fixture = json.loads((root / 'qualification/refresh-live-20260913.json').read_text())
fixture_id = fixture['dataset_id']
assert next(row['owner_id'] for row in rows if row['dataset_id'] == fixture_id) == fixture['owner_id'], 'restored fixture owner UUID changed'
fixture_owner = next(row['owner'] for row in rows if row['dataset_id'] == fixture_id)
status, login = request('/api/v1/auth/login', dict(username=fixture_owner, password=os.environ['COGNEE_INGEST_PASSWORD']), form=True)
assert status == 200, 'fixture operator login failed'
for search_type in ('CHUNKS', 'GRAPH_COMPLETION'):
    status, result = request('/api/v1/search', dict(dataset_ids=[fixture_id], query='Which service does FixtureRivet depend on?', search_type=search_type, only_context=True, include_references=True), login['access_token'])
    result_text = '\n'.join(strings(result))
    assert status == 200 and 'FixtureWillow' in result_text, 'restored fixture dependency missing'
    assert not any(marker in result_text for marker in ('FIXTURE_REMOVED_20260913', 'FixtureCedar', 'FixtureBirch', 'FixtureMaple')), 'restored deleted source or relation returned'
private_checks = 0
for domain, credential in credentials.items():
    status, result = request('/api/v1/auth/login', credential, form=True)
    assert status == 200, 'restored reader login failed'
    token = result['access_token']
    assert request('/api/v1/datasets', {'name': 'restore-denied-create'}, token)[0] == 403
    assert request('/api/v1/datasets', token=token)[0] == 403
    for dataset_id, packet_queries in queries.items():
        query = packet_queries[0]
        state = states[dataset_id]
        body = dict(domain=domain, dataset_ids=[dataset_id], search_type='CHUNKS', only_context=True, query=query, top_k=3, include_references=True)
        status, result = request('/api/v1/search', body, token)
        if state['domain'] in (domain, 'oss') and any(grant['email'] == credential['username'] and grant['dataset_id'] == dataset_id for grant in grants):
            assert status == 200 and result, 'restored scoped retrieval failed'
            for packet_query in packet_queries:
                body['query'] = packet_query
                status, result = request('/api/v1/search', body, token)
                assert status == 200 and any(packet_query in text for text in strings(result)), 'restored retrieval returned another source'
            if 'pilot' in next(row['dataset_name'] for row in rows if row['dataset_id'] == dataset_id):
                body['search_type'] = 'GRAPH_COMPLETION'
                status, result = request('/api/v1/search', body, token)
                assert status == 200 and result, 'restored pilot graph retrieval failed'
        else:
            assert_denied(status, result, allowed=(403,) if state['domain'] not in (domain, 'oss') else (403, 409))
            if state['domain'] == domain:
                private_checks += 1
    owned = next(key for key in queries if states[key]['domain'] == domain and any(grant['email'] == credential['username'] and grant['dataset_id'] == key for grant in grants))
    assert request('/api/v1/datasets/' + owned, token=token, method='DELETE')[0] == 403
    owner = next(row['owner'] for row in rows if row['dataset_id'] == owned)
    status, result = request('/api/v1/auth/login', dict(username=owner, password=os.environ['COGNEE_INGEST_PASSWORD']), form=True)
    assert status == 200, 'restored operator login failed'
    operator = result['access_token']
    readers = json.loads(os.environ['COGNEE_READER_DOMAINS'])
    reader_id = next(key for key, value in readers.items() if value == domain)
    status, _ = request('/api/v1/permissions/datasets/' + reader_id + '?permission_name=read', [owned], operator, method='DELETE')
    assert status == 200, 'restored grant revocation failed'
    body = dict(domain=domain, dataset_ids=[owned], search_type='CHUNKS', only_context=True, query=queries[owned][0], include_references=True)
    assert_denied(*request('/api/v1/search', body, token))
    status, _ = request('/api/v1/permissions/datasets/' + reader_id + '?permission_name=read', [owned], operator)
    assert status == 200, 'restored grant reissue failed'
    status, result = request('/api/v1/search', body, token)
    assert status == 200 and result, 'reissued reader grant does not retrieve'
    # Delete only restored data, then prove the source cannot reappear in search.
    status, _ = request('/api/v1/datasets/' + owned, token=operator, method='DELETE')
    assert status in (200, 204), 'restored dataset deletion failed'
    assert_denied(*request('/api/v1/search', body, token))
assert private_checks, 'same-domain private source denial was not exercised'
print(json.dumps({'restore_sources': len(rows), 'restore_datasets': len(queries), 'reader_domains': sorted(credentials), 'file_provenance': 'pass', 'scoped_search': 'pass', 'denied_mutations': 'pass', 'revoked_and_deleted': 'pass'}))
'''
probe = probe.replace("INPUT", repr((rows, credentials, grants)), 1)
subprocess.run(kube[:5] + ["exec", "-i", "cognee", "--", "/app/.venv/bin/python", "-"], input=probe, text=True, check=True)
