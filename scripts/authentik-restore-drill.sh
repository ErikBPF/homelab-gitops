#!/usr/bin/env bash
set -euo pipefail
exec python3 - "$@" <<'PY'
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

if len(sys.argv) != 2:
    sys.exit("usage: authentik-restore-drill.sh BUNDLE")
bundle = Path(sys.argv[1])
try:
    required = ["postgres.dump", "created-at", "SHA256SUMS"]
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError()
    for name in required:
        path = bundle / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise ValueError()
    timestamp = (bundle / "created-at").read_text().strip()
    if not re.fullmatch(r"[0-9]{1,12}", timestamp) or not 0 <= time.time() - int(timestamp) <= 86400:
        raise ValueError()
    lines = (bundle / "SHA256SUMS").read_text().splitlines()
    if len(lines) != 2:
        raise ValueError()
    for line, name in zip(lines, required[:2]):
        with (bundle / name).open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if line != f"{digest}  {name}":
            raise ValueError()
except (OSError, UnicodeError, ValueError):
    sys.exit("authentik restore: invalid, stale or corrupt bundle")

name = "authentik-restore-" + uuid.uuid4().hex[:16]
labels = {"recovery-run": name}
metadata = {"name": name, "namespace": "authentik", "labels": labels}
policy = {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": metadata,
          "spec": {"podSelector": {"matchLabels": labels}, "policyTypes": ["Ingress", "Egress"],
                   "ingress": [], "egress": []}}
postgres = "harbor.homelab.pastelariadev.com/dockerhub/library/postgres:17.10-bookworm@sha256:9b18b78397054fce88a9552e9d5a3ad5bb7fd258c5b3cc1c5028e46373d6ea8f"
authentik = "harbor.homelab.pastelariadev.com/ghcr/goauthentik/server:2026.5.6@sha256:ed120caf710ccf82ef0026f0bc74e51615bc95ebff228a7a2d6fc60c441c3868"
pod = {"apiVersion": "v1", "kind": "Pod", "metadata": metadata, "spec": {
    "restartPolicy": "Never", "activeDeadlineSeconds": 900, "automountServiceAccountToken": False,
    "securityContext": {"runAsNonRoot": True, "fsGroup": 999, "seccompProfile": {"type": "RuntimeDefault"}},
    "volumes": [{"name": "database", "emptyDir": {"sizeLimit": "10Gi"}}],
    "containers": [
        {"name": "postgres", "image": postgres,
         "args": ["postgres", "-c", "listen_addresses=127.0.0.1"],
         "securityContext": {"runAsUser": 999, "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
         "env": [{"name": "PGDATA", "value": "/var/lib/postgresql/data/pgdata"},
                 {"name": "POSTGRES_USER", "value": "authentik"},
                 {"name": "POSTGRES_DB", "value": "authentik"},
                 {"name": "POSTGRES_HOST_AUTH_METHOD", "value": "trust"}],
         "volumeMounts": [{"name": "database", "mountPath": "/var/lib/postgresql/data"}],
         "readinessProbe": {"exec": {"command": ["pg_isready", "-h", "127.0.0.1", "-U", "authentik", "-d", "authentik"]}, "periodSeconds": 2},
         "resources": {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"cpu": "1", "memory": "1Gi"}}},
        {"name": "authentik", "image": authentik,
         "command": ["python", "-c", "import time; time.sleep(900)"],
         "securityContext": {"runAsUser": 1000, "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
         "env": [{"name": "AUTHENTIK_SECRET_KEY", "valueFrom": {"secretKeyRef": {"name": "authentik-runtime", "key": "AUTHENTIK_SECRET_KEY"}}},
                 {"name": "AUTHENTIK_POSTGRESQL__HOST", "value": "127.0.0.1"},
                 {"name": "AUTHENTIK_POSTGRESQL__NAME", "value": "authentik"},
                 {"name": "AUTHENTIK_POSTGRESQL__USER", "value": "authentik"},
                 {"name": "AUTHENTIK_POSTGRESQL__PASSWORD", "value": ""},
                 {"name": "AUTHENTIK_ERROR_REPORTING__ENABLED", "value": "false"}],
         "resources": {"requests": {"cpu": "100m", "memory": "512Mi"}, "limits": {"cpu": "1", "memory": "1Gi"}}}
    ]}}

check = '''import secrets, uuid
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from authentik.core.models import Application, Group, User
from authentik.flows.models import Flow, FlowStageBinding
executor = MigrationExecutor(connection)
assert not executor.migration_plan(executor.loader.graph.leaf_nodes())
assert User.objects.exists() and Group.objects.exists() and Flow.objects.exists()
assert Application.objects.filter(provider__isnull=False).exists()
assert FlowStageBinding.objects.exists()
with transaction.atomic():
    user = User.objects.create(username="restore-" + uuid.uuid4().hex, name="Restore canary")
    password = secrets.token_urlsafe(32)
    user.set_password(password, signal=False)
    user.save()
    user.refresh_from_db()
    assert user.check_password(password)
    assert not user.check_password(password + "wrong")
    transaction.set_rollback(True)
'''

base = ["kubectl", "--context", "homelab", "-n", "authentik"]
def run(*args, manifest=None):
    return subprocess.run(base + list(args), input=json.dumps(manifest).encode() if manifest else None,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=600, check=True)

owned = []
phase = "isolation"
status = 0
try:
    run("create", "-f", "-", manifest=policy)
    owned.append("networkpolicy")
    phase = "scratch pod"
    run("create", "-f", "-", manifest=pod)
    owned.append("pod")
    run("wait", "--for=condition=Ready", "pod/" + name, "--timeout=180s")
    phase = "database restore"
    run("cp", str((bundle / "postgres.dump").resolve()), name + ":/tmp/authentik.dump", "-c", "postgres")
    run("exec", name, "-c", "postgres", "--", "pg_restore", "--exit-on-error",
        "--no-owner", "--no-privileges", "-U", "authentik", "-d", "authentik", "/tmp/authentik.dump")
    phase = "application check"
    run("exec", name, "-c", "authentik", "--", "ak", "shell", "-c", check)
except (OSError, subprocess.SubprocessError) as error:
    status = error.returncode if isinstance(error, subprocess.CalledProcessError) else 1
    print(f"authentik restore: {phase} failed", file=sys.stderr)
finally:
    for kind in reversed(owned):
        try:
            run("delete", kind, name, "--wait=true", "--timeout=60s")
        except (OSError, subprocess.SubprocessError):
            status = status or 1
            print(f"authentik restore: cleanup failed for {kind} {name}", file=sys.stderr)
            break  # Keep the deny policy if the scratch pod could not be removed.
if status:
    sys.exit(status)
print(json.dumps({"status": "passed", "source_epoch": int(timestamp), "checked_epoch": int(time.time()),
                  "postgres_image": postgres, "authentik_image": authentik,
                  "scope": "isolated restore and password handling; not MFA/OIDC or cold OpenBao recovery"}))
PY
