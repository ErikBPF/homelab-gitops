#!/usr/bin/env python3
"""Prove Wazuh database daemons stopped before the Pod can publish a capture marker.

Filebeat is deliberately outside this proof. A marker is bound to one Pod UID.
"""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time

MARKER = Path('/var/ossec/queue/.host-integrity-stop.json')


def stop():
    process = subprocess.Popen(['/var/ossec/bin/wazuh-control', 'stop'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    try:
        if process.wait(timeout=60) != 0:
            raise ValueError('official stop failed')
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        raise


def remaining(proc=Path('/proc')):
    for path in proc.iterdir():
        if not path.name.isdecimal() or int(path.name) == os.getpid():
            continue
        try:
            command = (path / 'comm').read_text().strip()
            with (path / 'cmdline').open('rb') as stream:
                args = stream.read(65537)
        except FileNotFoundError:
            continue
        if len(args) > 65536:
            raise ValueError('process inventory exceeded bound')
        if command.startswith('wazuh-') or b'/var/ossec/api/' in args:
            return True
    return False


def shutdown(uid, marker=MARKER, proc=Path('/proc')):
    marker = Path(marker)
    # Invalidate first: even a malformed invocation cannot leave old successful proof.
    marker.unlink(missing_ok=True)
    directory = os.open(marker.parent, os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)
    if not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', uid):
        raise ValueError('missing Pod identity')
    stop()
    if remaining(proc):
        raise ValueError('Wazuh daemon remains after stop')
    result = {'schema': 1, 'pod_uid': uid, 'stopped_at': int(time.time()),
              'wazuh_databases_gracefully_stopped': True,
              'filebeat_consistency_proven': False}
    fd, temporary = tempfile.mkstemp(prefix='.stop-', dir=marker.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(result, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
        directory = os.open(marker.parent, os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


if __name__ == '__main__':
    try:
        shutdown(os.environ.get('POD_UID', ''))
    except (OSError, ValueError, subprocess.SubprocessError):
        raise SystemExit('Wazuh graceful database stop not proven')
