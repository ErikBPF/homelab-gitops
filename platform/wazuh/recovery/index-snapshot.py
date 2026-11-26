#!/usr/bin/env python3
"""Prepare native snapshots with a dedicated projected identity and verified cleanup."""
import datetime
import base64
import hashlib
import tarfile
import stat
import shutil
import os
import re
from pathlib import Path
import ssl
import urllib.request
import json
import subprocess
import time
import uuid

REPOSITORY = 'wazuh-recovery'
LOCATION = '/var/lib/wazuh-indexer/recovery'
AUTH = Path('/run/snapshot-auth')
CA = '/run/indexer-ca/root-ca.pem'


def api(method, path, body=None):
    # A dedicated projected runtime identity, never the scale controller token or
    # a human password. Kubernetes projection is trusted; values never leave memory.
    credentials = []
    for field in ('username', 'password'):
        with (AUTH / field).open('rb') as stream:
            value = stream.read(4097).strip()
        if not value or len(value) > 4096 or b'\n' in value or b'\r' in value:
            raise ValueError('invalid snapshot credential file')
        credentials.append(value)
    if b':' in credentials[0]:
        raise ValueError('invalid snapshot username')
    headers = {'Authorization': 'Basic ' + base64.b64encode(b':'.join(credentials)).decode(),
               'Content-Type': 'application/json'}
    request = urllib.request.Request('https://indexer:9200' + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers, method=method)
    context = ssl.create_default_context(cafile=CA)
    with urllib.request.urlopen(request, context=context, timeout=120) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('snapshot API response exceeds limit')
    return json.loads(raw)


def snapshot():
    started = int(time.time())
    # No existing repository is silently repointed, and no prior snapshot is deleted.
    repositories = api('GET', '/_snapshot')
    expected = {'type': 'fs', 'settings': {'location': LOCATION, 'compress': True}}
    if REPOSITORY in repositories:
        repo = repositories[REPOSITORY]
        if repo.get('type') != 'fs' or repo.get('settings', {}).get('location') != LOCATION:
            raise ValueError('unexpected existing snapshot repository')
    else:
        directory = Path(LOCATION)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        if api('PUT', '/_snapshot/' + REPOSITORY, expected).get('acknowledged') is not True:
            raise ValueError('snapshot repository was not acknowledged')
    if api('GET', '/_snapshot/' + REPOSITORY + '/_current').get('snapshots'):
        raise ValueError('snapshot repository is busy')
    if len(api('GET', '/_snapshot/' + REPOSITORY + '/_all').get('snapshots', [])) >= 32:
        raise ValueError('native snapshot maintenance limit reached')
    name = 'wazuh-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dt%H%M%Sz').lower() + '-' + uuid.uuid4().hex[:8]
    data = api('PUT', f'/_snapshot/{REPOSITORY}/{name}?wait_for_completion=true', {
        'indices': 'wazuh-*',
        'ignore_unavailable': False, 'include_global_state': False, 'partial': False,
    }).get('snapshot', {})
    shards = data.get('shards', {})
    if (not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', data.get('uuid', ''))
            or data.get('state') != 'SUCCESS' or shards.get('failed') != 0
            or not isinstance(shards.get('total'), int) or shards['total'] < 1
            or shards.get('successful') != shards['total'] or not data.get('indices')):
        raise ValueError('native snapshot was incomplete')
    return {'started': started, 'completed': int(time.time()), 'indexer_snapshot': name,
            'indexer_state': 'SUCCESS', 'failed_shards': 0,
            'indices': sorted(data['indices']), 'repository': REPOSITORY,
            'native_uuid': data.get('uuid')}


def cleanup_verified(base, proof_root=Path('/run/recovery-proof')):
    for directory in base.iterdir():
        if not re.fullmatch(r'wazuh-[0-9]{8}t[0-9]{6}z-[a-f0-9]{8}', directory.name) or directory.is_symlink():
            continue
        parent = os.open(proof_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            owner = os.fstat(parent)
            if owner.st_uid != 0 or owner.st_mode & 0o022:
                raise ValueError('proof parent is writable outside root owner')
            try:
                fd = os.open(directory.name + '.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            except FileNotFoundError:
                continue
        finally:
            os.close(parent)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode):
                raise ValueError('cleanup receipt is not root-controlled')
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError('cleanup receipt exceeds limit')
        proof = json.loads(raw)
        with (directory / 'indexer-capture.json').open('rb') as stream:
            metadata = json.loads(stream.read(16384))
        if (proof.get('indexer_readback_verified') is not True
                or proof.get('indexer_snapshot') != directory.name
                or proof.get('indexer_sha256') != metadata.get('sha256')
                or proof.get('native_uuid') != metadata.get('native_uuid')
                or not re.fullmatch(r'[a-f0-9]{64}', proof.get('snapshot_id', ''))):
            raise ValueError('cleanup receipt does not prove this encrypted archive')
        try:
            current = api('GET', f'/_snapshot/{REPOSITORY}/{directory.name}').get('snapshots', [])
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            # A prior acknowledged DELETE may have completed before a crash.
            # The verified encrypted archive still authorizes local retirement.
            shutil.rmtree(directory)
            continue
        if len(current) != 1 or current[0].get('uuid') != proof['native_uuid']:
            raise ValueError('native snapshot identity changed')
        if api('DELETE', f'/_snapshot/{REPOSITORY}/{directory.name}').get('acknowledged') is not True:
            raise ValueError('owned native snapshot cleanup failed')
        shutil.rmtree(directory)


def export_capture(receipt, base=Path('/var/lib/wazuh-indexer/recovery-captures')):
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    # A crashed or failed export leaves staging behind; it is never valid input
    # and it counts against the budget, so reclaim it before the next attempt.
    for stale in base.glob('.incomplete-*'):
        shutil.rmtree(stale, ignore_errors=True)
    if len(list(base.iterdir())) >= 3:
        raise ValueError('unconsumed capture limit reached')
    name = receipt['indexer_snapshot']
    temporary = base / ('.incomplete-' + name)
    temporary.mkdir(mode=0o700)
    archive = temporary / 'indexer.tar'
    total = count = 0
    deadline = time.monotonic() + 120
    try:
        with tarfile.open(archive, 'w') as output:
            for directory, dirs, files in os.walk(LOCATION, followlinks=False):
                for child in [*dirs, *files]:
                    source = Path(directory) / child
                    info = source.lstat()
                    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                        raise ValueError('unsafe native repository member')
                    total += info.st_size if stat.S_ISREG(info.st_mode) else 0
                    count += 1
                    if total > 12 * 1024 ** 3 or count > 250000 or time.monotonic() > deadline:
                        raise ValueError('snapshot export budget exceeded')
                    output.add(source, arcname=str(source.relative_to(LOCATION)), recursive=False)
        archive.chmod(0o600)
        digest = hashlib.sha256()
        with archive.open('rb') as stream:
            while chunk := stream.read(1024 * 1024): digest.update(chunk)
        receipt = dict(receipt, sha256=digest.hexdigest(),
                       indexer_image=os.environ['SOURCE_INDEXER_IMAGE'])
        metadata = temporary / 'indexer-capture.json'
        metadata.write_text(json.dumps(receipt))
        metadata.chmod(0o600)
        for path in (archive, metadata):
            with path.open('rb') as stream: os.fsync(stream.fileno())
        os.rename(temporary, base / name)
        directory = os.open(base, os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
        return name
    except BaseException:
        # Keep incomplete evidence private; never publish its directory as complete.
        shutil.rmtree(temporary, ignore_errors=True)
        raise


if __name__ == '__main__':
    try:
        os.umask(0o077)
        base = Path('/var/lib/wazuh-indexer/recovery-captures')
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        cleanup_verified(base)
        # A hard kill leaves staging behind; reclaim it before the budget gate,
        # or three of them wedge every later run.
        for stale in base.glob('.incomplete-*'):
            shutil.rmtree(stale, ignore_errors=True)
        if len(list(base.iterdir())) >= 3:
            raise ValueError('unconsumed capture limit reached')
        print(json.dumps({'capture_id': export_capture(snapshot(), base)}))
    except (OSError, ValueError, subprocess.SubprocessError):
        raise SystemExit('Wazuh native snapshot failed; no complete capture receipt')
