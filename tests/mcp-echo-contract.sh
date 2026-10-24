#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
app=$root/apps/homelab/mcp-echo
render=$(mktemp)
trap 'rm -f "$render"' EXIT

test -f "$app/Chart.yaml" || { echo 'mcp-echo chart missing' >&2; exit 1; }
test "$(yq -r '.apiVersion' "$app/Chart.yaml")" = v2
test "$(yq -r '.name' "$app/Chart.yaml")" = mcp-echo-wrapper
test "$(yq -r '.version' "$app/Chart.yaml")" = 1.0.0
test "$(yq -r '(.dependencies // []) | length' "$app/Chart.yaml")" = 0
if test -f "$root/clusters/kepler/apps/mcp-echo.yaml"; then
  test "$(yq -r '.spec.project' "$root/clusters/kepler/apps/mcp-echo.yaml")" = homelab
else
  echo 'mcp-echo Application is orchestrator-owned; skipping project check'
fi

helm template mcp-echo "$app" >"$render"
python3 "$root/scripts/check-first-wave-resources.py" "$render"
python3 - "$render" <<'PY'
import http.client
import io
import json
import logging
import sys
import threading

import yaml

# Given the rendered chart, when inspecting its resources, then enforce isolation.
with open(sys.argv[1]) as stream:
    documents = [doc for doc in yaml.safe_load_all(stream) if doc]
for doc in documents:
    assert doc['kind'] in {'Deployment', 'Service', 'ConfigMap', 'NetworkPolicy'}, doc['kind']
    assert doc['metadata']['namespace'] == 'mcp-echo'
    spec = doc.get('spec', {})
    if doc['kind'] == 'CronJob':
        spec = spec['jobTemplate']['spec']
    pod = spec.get('template', {}).get('spec', {})
    for container in pod.get('initContainers', []) + pod.get('containers', []):
        assert '@sha256:' in container['image'], container['image']
deployment, = [doc for doc in documents if doc['kind'] == 'Deployment']
assert deployment['spec']['replicas'] == 1
pod = deployment['spec']['template']['spec']
container, = pod['containers']
assert container['securityContext']['readOnlyRootFilesystem'] is True
assert container['securityContext']['allowPrivilegeEscalation'] is False
assert container['securityContext']['capabilities']['drop'] == ['ALL']
assert pod['securityContext']['runAsNonRoot'] is True
assert pod['automountServiceAccountToken'] is False
config, = [doc for doc in documents if doc['kind'] == 'ConfigMap']
volume, = [v for v in pod['volumes'] if v.get('configMap', {}).get('name') == config['metadata']['name']]
assert volume['configMap']['defaultMode'] == 0o555
mount, = [m for m in container['volumeMounts'] if m['name'] == volume['name']]
assert mount['readOnly'] is True
assert container['command'] == ['python3']
assert container['args'][0] == mount['mountPath'] + '/server.py'
assert container['args'][1:] == ['--port', '8080']
for probe in ('readinessProbe', 'livenessProbe'):
    assert container[probe]['httpGet'] == {'path': '/headers', 'port': 'http'}
service, = [doc for doc in documents if doc['kind'] == 'Service']
assert service['metadata']['name'] == 'mcp-echo'
assert service['spec']['type'] == 'ClusterIP'
assert service['spec']['ports'] == [{'name': 'http', 'port': 8080, 'targetPort': 'http', 'protocol': 'TCP'}]
assert service['spec']['selector'] == deployment['spec']['selector']['matchLabels']
policies = {doc['metadata']['name']: doc['spec'] for doc in documents if doc['kind'] == 'NetworkPolicy'}
assert policies['default-deny'] == {'podSelector': {}, 'policyTypes': ['Ingress', 'Egress']}
runtime = policies['allow-runtime']
assert runtime['ingress'] == [{'from': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'openwebui'}}}], 'ports': [{'protocol': 'TCP', 'port': 8080}]}]
assert runtime['egress'] == [{'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}}}], 'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}]}]
script = config['data']['server.py']
for marker in ('/v1/chat/completions', '/mcp', 'X-OpenWebUI'):
    assert marker in script, marker
assert len(script.splitlines()) <= 120

# Given the actual embedded server, when making HTTP requests, then verify wire behavior.
namespace = {'__name__': 'contract'}
exec(compile(script, 'server.py', 'exec'), namespace)
logs = io.StringIO()
handler = logging.StreamHandler(logs)
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)
headers = [('X-OpenWebUI-User-Id', 'probe-user'), ('X-Duplicate', 'first'), ('X-Duplicate', 'second'), ('Authorization', 'Bearer disposable-probe')]

def request(method, path, payload=None, accept='application/json'):
    body = json.dumps(payload) if payload is not None else ''
    connection = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
    try:
        connection.putrequest(method, path)
        for name, value in headers:
            connection.putheader(name, value)
        connection.putheader('Accept', accept)
        connection.putheader('Content-Type', 'application/json')
        connection.putheader('Content-Length', str(len(body.encode())))
        connection.endheaders(body.encode())
        response = connection.getresponse()
        return response.status, response.getheader('Content-Type'), response.read().decode()
    finally:
        connection.close()

with namespace['ThreadingTCPServer'](('127.0.0.1', 0), namespace['Handler']) as server:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, content_type, body = request('GET', '/headers')
        assert status == 200 and content_type == 'application/json'
        received = json.loads(body)
        assert all(list(pair) in received for pair in headers)
        assert '\n  [' in body, 'headers must be pretty JSON'
        status, _, body = request('GET', '/v1/models')
        assert status == 200 and json.loads(body)['data'][0]['id'] == 'echo-stub'
        status, _, body = request('POST', '/v1/chat/completions', {'model': 'requested-model', 'messages': [{'content': 'BODY-MUST-NOT-APPEAR'}]})
        chat = json.loads(body)
        assert status == 200 and chat['model'] == 'requested-model'
        assert chat['id'] == 'chatcmpl-echo' and chat['object'] == 'chat.completion'
        assert isinstance(chat['created'], int)
        assert chat['choices'] == [{'index': 0, 'message': {'role': 'assistant', 'content': 'echo-stub: headers received'}, 'finish_reason': 'stop'}]
        assert chat['usage'] == {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}
        for accept in ('application/json, text/event-stream', 'text/event-stream'):
            status, content_type, body = request('POST', '/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}}, accept)
            if accept == 'text/event-stream':
                assert content_type == 'text/event-stream'
                body = body.split('data: ', 1)[1].strip()
            else:
                assert content_type == 'application/json'
            rpc = json.loads(body)
            assert status == 200 and rpc['id'] == 1 and rpc['jsonrpc'] == '2.0'
            assert rpc['result']['protocolVersion'] == '2025-06-18'
            assert rpc['result']['capabilities'] == {'tools': {}}
        assert request('POST', '/mcp', {'jsonrpc': '2.0', 'method': 'notifications/initialized'})[::2] == (202, '')
        status, _, body = request('POST', '/mcp', {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        assert status == 200 and [t['name'] for t in json.loads(body)['result']['tools']] == ['echo_headers']
        status, _, body = request('POST', '/mcp', {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'echo_headers'}})
        received = json.loads(json.loads(body)['result']['content'][0]['text'])
        assert status == 200 and all(list(pair) in received for pair in headers)
        status, _, body = request('POST', '/mcp', {'jsonrpc': '2.0', 'id': 4, 'method': 'missing'})
        assert status == 200 and json.loads(body)['error']['code'] == -32601
        assert request('POST', '/mcp', [1, 2])[0] == 400
        assert request('GET', '/mcp')[0] == 405
        assert request('DELETE', '/unknown')[0] == 501
        output = logs.getvalue()
        for name, value in headers:
            assert f'{name}: {value}' in output
        for method, path in [('GET', '/headers'), ('POST', '/v1/chat/completions'), ('POST', '/mcp'), ('DELETE', '/unknown')]:
            assert f'{method} {path}' in output
        assert 'BODY-MUST-NOT-APPEAR' not in output
    finally:
        server.shutdown()
        thread.join()
        logging.getLogger().removeHandler(handler)
print('mcp-echo contract: manifests and live HTTP checks passed')
PY
