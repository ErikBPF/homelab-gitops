#!/usr/bin/env python3
"""Validate a retrieved Wazuh bundle in private scratch and a networkless indexer.

Run explicitly on Kepler after source review. No live manager is started and no
production credentials or endpoints enter the disposable indexer.
"""
import argparse
from contextlib import closing
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import selectors
import signal
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import urllib.parse
import uuid

IMAGE = 'harbor.homelab.pastelariadev.com/dockerhub/wazuh/wazuh-indexer:4.14.7@sha256:66b7640cce54f5f20a65e8320601b4570a1306d9f9b334d30bcaa324720a517c'
PIN = '3D:53:33:FB:58:6F:22:DA:2A:72:71:23:9F:F4:3A:EB:B0:F9:20:3F:35:2D:0A:66:76:94:00:B4:34:9B:05:27'.replace(':', '')
ARCHIVES = ('indexer.tar', 'manager-master.tar', 'manager-worker.tar')
MAX_BYTES = 12 * 1024 ** 3


def command(args, *, timeout=30, data=None):
    limit = 1024 * 1024
    if data is not None and len(data) > limit:
        raise ValueError('unexpected command input')
    # A private seekable input avoids blocking on a child that never reads stdin.
    with tempfile.TemporaryFile() as incoming:
        incoming.write(data or b''); incoming.seek(0)
        process = subprocess.Popen(args, stdin=incoming, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + timeout
        output = bytearray()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(args, timeout)
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fd, min(65536, limit + 1 - len(output)))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        output.extend(chunk)
                        if len(output) > limit:
                            raise ValueError('unexpected diagnostic output')
            code = process.wait(timeout=max(0, deadline - time.monotonic()))
            if code:
                raise subprocess.CalledProcessError(code, args)
            return bytes(output)
        finally:
            # Kill the group even if its leader exited while descendants kept stdout.
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait()
            process.stdout.close()


def validate_bundle(bundle):
    bundle = Path(bundle)
    if bundle.is_symlink() or not bundle.is_dir() or bundle.stat().st_mode & 0o077:
        raise ValueError('retrieved bundle must be private')
    with (bundle / 'capture.json').open('rb') as stream: raw = stream.read(16385)
    if len(raw) > 16384: raise ValueError('manifest too large')
    manifest = json.loads(raw)
    if (manifest.get('indexer_image') != IMAGE or manifest.get('authd_sha256') != PIN
            or manifest.get('indexer_state') != 'SUCCESS' or manifest.get('failed_shards') != 0
            or manifest.get('wazuh_databases_gracefully_stopped') is not True
            or manifest.get('managers_recovered') is not True
            or not re.fullmatch(r'wazuh-[a-z0-9-]{1,64}', manifest.get('indexer_snapshot', ''))
            or not manifest.get('indices')
            or any(not isinstance(name, str) or not name.startswith('wazuh-') for name in manifest['indices'])):
        raise ValueError('capture is not a complete reviewed recovery bundle')
    start, end = manifest.get('started'), manifest.get('completed')
    # Recovery must remain possible from older verified archives. Publication
    # enforces freshness; the restore report exposes the actual recovery age.
    if type(start) is not int or type(end) is not int or not 0 < start <= end <= time.time():
        raise ValueError('capture has invalid recovery timestamps')
    total = 0
    for name in ARCHIVES:
        path = bundle / name
        if path.is_symlink() or not path.is_file(): raise ValueError('unsafe archive')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_BYTES: raise ValueError('bundle exceeds restore budget')
                digest.update(chunk)
        if digest.hexdigest() != manifest.get('sha256', {}).get(name):
            raise ValueError('archive checksum mismatch')
    return manifest


def extract(archive, target):
    target.mkdir(mode=0o700)
    with tarfile.open(archive, 'r:') as source:
        members = list(itertools.islice(source, 250001))
        if len(members) > 250000 or sum(member.size for member in members) > MAX_BYTES:
            raise ValueError('expanded archive exceeds restore budget')
        # Python's data filter rejects absolute/traversing links and special files.
        source.extractall(target, members=members, filter='data')


def manager_checks(root, verify_pin):
    etc = root / 'wazuh/var/ossec/etc'
    databases = list((root / 'wazuh/var/ossec/queue/db').glob('*.db'))
    if not databases: raise ValueError('manager databases missing')
    for database in databases:
        if not database.resolve().is_relative_to(root.resolve()):
            raise ValueError('database escaped restored directory')
        uri = 'file:' + urllib.parse.quote(str(database)) + '?mode=ro&immutable=1'
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            deadline = time.monotonic() + 60
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
            if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('manager database integrity failed')
    if verify_pin:
        certificate, key = etc / 'sslmanager.cert', etc / 'sslmanager.key'
        for path in (certificate, key):
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError('TLS material escaped restored directory')
        der = command(['openssl', 'x509', '-in', str(certificate), '-outform', 'DER'])
        if hashlib.sha256(der).hexdigest().upper() != PIN:
            raise ValueError('restored enrollment certificate differs from Desktop pin')
        public = command(['openssl', 'x509', '-in', str(certificate), '-pubkey', '-noout'])
        cert_public = command(['openssl', 'pkey', '-pubin', '-outform', 'DER'], data=public)
        key_public = command(['openssl', 'pkey', '-in', str(key), '-pubout', '-outform', 'DER'])
        if cert_public != key_public: raise ValueError('restored enrollment key does not match certificate')
    return len(databases)


def indexer_restore(repository, data, configuration, manifest, engine='docker'):
    name = 'wazuh-restore-' + uuid.uuid4().hex
    # Scratch parent stays0700. Only these mounts become readable by UID1000 inside
    # the networkless container; no standing credentials or live PVC is mounted.
    for directory, dirs, files in os.walk(repository):
        Path(directory).chmod(0o755)
        for file in files: (Path(directory) / file).chmod(0o644)
    data.mkdir(mode=0o700); os.chown(data, 1000, 1000)
    configuration.write_text('cluster.name: isolated-wazuh-restore\nnode.name: fixture\ndiscovery.type: single-node\nnetwork.host: 127.0.0.1\npath.data: /var/lib/wazuh-indexer\npath.logs: /tmp/indexer-logs\npath.repo: [/restore-repository]\nplugins.security.disabled: true\n')
    configuration.chmod(0o644)
    def api(method, path, body=None):
        args = [engine, 'exec', name, 'curl', '--silent', '--fail', '--max-time', '10',
                '-X', method, 'http://127.0.0.1:9200' + path]
        if body is not None: args += ['-H', 'Content-Type: application/json', '--data-binary', json.dumps(body)]
        return json.loads(command(args, timeout=15))
    try:
        command([engine, 'run', '-d', '--rm', '--pull=never', '--name', name,
                 '--network=none', '--memory=2g', '--cpus=1', '--pids-limit=256',
                 '--cap-drop=ALL', '--security-opt=no-new-privileges', '--user=1000:1000',
                 '--env', 'OPENSEARCH_JAVA_OPTS=-Xms512m -Xmx512m',
                 '--volume', f'{repository}:/restore-repository:ro',
                 '--volume', f'{data}:/var/lib/wazuh-indexer',
                 '--volume', f'{configuration}:/usr/share/wazuh-indexer/config/opensearch.yml:ro',
                 '--entrypoint', '/usr/share/wazuh-indexer/bin/opensearch', IMAGE], timeout=30)
        deadline = time.monotonic() + 120
        while True:
            try:
                api('GET', '/'); break
            except (ValueError, subprocess.SubprocessError):
                if time.monotonic() >= deadline: raise ValueError('isolated indexer readiness expired')
                time.sleep(1)
        if api('PUT', '/_snapshot/recovery', {'type': 'fs', 'settings': {'location': '/restore-repository', 'readonly': True}}).get('acknowledged') is not True:
            raise ValueError('isolated repository registration failed')
        native = api('GET', '/_snapshot/recovery/' + manifest['indexer_snapshot']).get('snapshots', [])
        if len(native) != 1 or native[0].get('uuid') != manifest.get('native_uuid'):
            raise ValueError('restored native snapshot UUID differs from capture')
        result = api('POST', '/_snapshot/recovery/' + manifest['indexer_snapshot'] + '/_restore?wait_for_completion=true',
                     {'indices': 'wazuh-*', 'include_global_state': False,
                      'index_settings': {'index.number_of_replicas': 0}}).get('snapshot', {})
        shards = result.get('shards', {})
        if shards.get('failed') != 0 or not shards.get('total') or shards.get('successful') != shards.get('total'):
            raise ValueError('isolated native restore incomplete')
        inventory = api('GET', '/_cat/indices/wazuh-*?format=json&h=index,docs.count')
        if {row['index'] for row in inventory} != set(manifest['indices']):
            raise ValueError('restored index inventory differs from capture')
        return sum(int(row['docs.count']) for row in inventory)
    finally:
        command([engine, 'rm', '-f', name], timeout=15)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--engine', choices=['docker', 'podman'], default='docker')
    args = parser.parse_args()
    manifest = validate_bundle(args.bundle)
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='.wazuh-drill-', dir=args.bundle.parent) as temporary:
        root = Path(temporary)
        # Every archive is bounded by MAX_BYTES on its own, and the restore then
        # writes primaries and translog beside them, so keep one extra budget
        # for the restored data.
        required = (len(ARCHIVES) + 1) * MAX_BYTES
        if shutil.disk_usage(root).free < required:
            raise ValueError(
                f'isolated restore requires {required // 1024 ** 3}GiB '
                'free scratch budget'
            )
        for name in ARCHIVES: extract(args.bundle / name, root / name.removesuffix('.tar'))
        counts = [manager_checks(root / 'manager-master', True), manager_checks(root / 'manager-worker', False)]
        documents = indexer_restore(root / 'indexer', root / 'data', root / 'opensearch.yml', manifest, args.engine)
    print(json.dumps({'restored': True, 'manager_database_counts': counts, 'index_document_count': documents,
                      'source_age_seconds': int(time.time()) - manifest['started'],
                      'enrollment_certificate_and_key_verified': True, 'filebeat_consistency_proven': False}))


if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, sqlite3.Error, tarfile.TarError, subprocess.SubprocessError):
        raise SystemExit('Wazuh isolated restore failed; no recovery proof')
