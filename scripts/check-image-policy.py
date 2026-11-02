#!/usr/bin/env python3
"""Enforce tag-plus-digest image refs, and optionally a minimum image age.

Policy mode (default) renders every Helm chart and kustomization under the
scanned directories, plus the plain manifests outside them, then rejects any
image reference without a tag, without a digest, with a rolling tag, or with a
placeholder (all-zero) digest. With --base it checks only the components the
diff touches, so existing refs do not block an unrelated change.

Age mode (--age --base REV) renders only the components touched by the diff
against REV, and only checks refs whose digest the diff adds, so pre-existing
stale pins do not block unrelated changes. It rejects images younger than
three days, measured from the OCI config `created` field, and a tag that no
longer resolves to the digest it names. Docker registries publish no release
timestamp, so this is the only place the hold can be checked.

Rendering is required because this repository spreads a pin across
`repository`, `tag` and `digest`/`sha` keys with values inherited from the
chart, so the source text does not describe the effective image. Helm
dependencies must already be built (rendering builds them on demand).
`harbor.homelab.pastelariadev.com/library/*` images are built here, so age mode
skips them. Components listed in `DIGEST_ONLY_COMPONENTS` render a digest-only
ref from a chart that ignores the pinned tag; they are exempt from the tag
requirement until that chart carries the tag.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - CI installs PyYAML
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("argocd", "apps", "bootstrap", "clusters", "platform")
SUFFIXES = (".yaml", ".yml")
EXCLUDED_PARTS = ("upstream/", "charts/", "worktrees/")
ROLLING_TAGS = {
    "latest", "main", "master", "stable", "edge", "release", "nightly",
    "dev", "rolling", "experimental", "canary",
}
ZERO_DIGEST = "sha256:" + "0" * 64
# Deliberate not-yet-published placeholder; drop the file from this set when
# the Aster digest is pinned.
PLACEHOLDER_FILES = {
    "apps/homelab/aster/deployment-controller.yaml",
    "apps/homelab/aster/deployment-server.yaml",
}
MIN_AGE = timedelta(days=3)
# Components whose vendored chart renders `repository@digest` even when the
# values pin a tag, so the effective ref cannot carry one. Shrinks as those
# charts gain tag support; drop entries when the rendered ref has a tag.
DIGEST_ONLY_COMPONENTS = {
    "apps/homelab/airflow",
    "platform/alloy-metrics",
    "platform/cert-manager",
    "platform/clickhouse-operator",
    "platform/jaeger",
    "platform/loki",
    "platform/otel-collector",
    "platform/traefik",
}
ACCEPT = (
    "application/vnd.oci.image.index.v1+json,"
    "application/vnd.oci.image.manifest.v1+json,"
    "application/vnd.docker.distribution.manifest.list.v2+json,"
    "application/vnd.docker.distribution.manifest.v2+json"
)
HARBOR_HOSTS = {
    "dockerhub": "docker.io",
    "ghcr": "ghcr.io",
    "quay": "quay.io",
    "k8s": "registry.k8s.io",
    "lscr": "lscr.io",
}
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def tracked():
    raw = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    return [name for name in raw.split("\0") if name]


def excluded(name):
    return any(part in name for part in EXCLUDED_PARTS)


def image_refs(document):
    found = []
    pending = [document]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            image = node.get("image")
            if isinstance(image, str) and image:
                found.append(image)
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return found


class Unit:
    def __init__(self, kind, path):
        self.kind = kind
        self.path = path

    def text(self):
        if self.kind == "helm":
            subprocess.run(
                ["helm", "dependency", "build", self.path],
                cwd=ROOT, check=False, capture_output=True, text=True,
            )
            command = ["helm", "template", Path(self.path).name, self.path]
        else:
            command = ["kubectl", "kustomize", self.path]
        return subprocess.run(
            command, cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout

    def refs(self):
        if yaml is None:
            return [
                match.group(1)
                for match in re.finditer(r"(?m)^\s*-?\s*image:\s*[\"']?([^\s\"']+)", self.text())
            ]
        found = []
        for document in yaml.safe_load_all(self.text()):
            found.extend(image_refs(document))
        return found


def units():
    files = [name for name in tracked() if not excluded(name)]
    helms = sorted({str(Path(name).parent) for name in files if name.endswith("Chart.yaml")})
    kustomizations = sorted(
        {str(Path(name).parent) for name in files if name.endswith("kustomization.yaml")}
    )
    owned_dirs = helms + kustomizations
    plain = []
    for name in files:
        if not name.endswith(SUFFIXES) or not name.startswith(SCAN_DIRS):
            continue
        directory = str(Path(name).parent)
        if any(directory == own or directory.startswith(f"{own}/") for own in owned_dirs):
            continue
        plain.append(name)
    found = [Unit("helm", path) for path in helms]
    found += [Unit("kustomize", path) for path in kustomizations]
    return found, plain


def split_ref(ref):
    name, sep, digest = ref.partition("@")
    if not sep:
        return name, None, None
    tag = None
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        name, tag = name.rsplit(":", 1)
    return name, tag, digest


def check(ref, where, allow_missing_tag=False):
    repo, tag, digest = split_ref(ref)
    if digest is None:
        return [f"{where}: missing @sha256 digest"]
    if not DIGEST_RE.match(digest):
        return [f"{where}: digest is not sha256:<64 lowercase hex>"]
    problems = []
    if tag is None:
        if not allow_missing_tag:
            problems.append(f"{where}: missing tag")
    elif tag.lower() in ROLLING_TAGS:
        problems.append(f"{where}: rolling tag {tag!r}")
    if digest == ZERO_DIGEST and where.split(":", 1)[0] not in PLACEHOLDER_FILES:
        problems.append(f"{where}: placeholder digest")
    return problems


def touched_units(base):
    changed = changed_files(base)
    rendered, plain = units()
    touched = [
        unit for unit in rendered
        if any(name.startswith(f"{unit.path}/") or name == unit.path for name in changed)
    ]
    return touched, [name for name in plain if name in changed]


def policy_violations(base):
    violations = []
    rendered, plain = (touched_units(base) if base else units())
    for unit in rendered:
        allow = unit.path in DIGEST_ONLY_COMPONENTS
        for ref in unit.refs():
            violations.extend(check(ref, f"{unit.path}: {ref}", allow_missing_tag=allow))
    for name in plain:
        text = (ROOT / name).read_text(encoding="utf-8")
        documents = []
        if yaml is not None:
            try:
                documents = list(yaml.safe_load_all(text))
            except yaml.YAMLError:
                documents = []
        refs = []
        for document in documents:
            refs.extend(image_refs(document))
        if not documents:
            refs = [
                match.group(1)
                for match in re.finditer(r"(?m)^\s*-?\s*image:\s*[\"']?([^\s\"']+)", text)
                if "{{" not in match.group(1)
            ]
        for ref in refs:
            violations.extend(check(ref, f"{name}: {ref}"))
    return violations


def changed_digests(base):
    """Digests introduced by the diff, so untouched stale pins are not checked."""
    merge_base = subprocess.run(
        ["git", "merge-base", base, "HEAD"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    diff = subprocess.run(
        ["git", "diff", "--unified=0", merge_base],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout
    return parse_added_digests(diff)


def parse_added_digests(diff_text):
    found = set()
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            found.update(re.findall(r"sha256:[0-9a-f]{64}", line))
    return found


def changed_files(base):
    merge_base = subprocess.run(
        ["git", "merge-base", base, "HEAD"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    raw = subprocess.run(
        ["git", "diff", "--name-only", merge_base],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout
    return [name for name in raw.splitlines() if name]


def _open(url, headers=None, attempts=3):
    last = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "image-policy/1", **(headers or {})}
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code not in (408, 425, 429) and not 500 <= error.code < 600:
                raise
            last = error
        except (urllib.error.URLError, TimeoutError) as error:
            last = error
        if attempt + 1 < attempts:
            time.sleep(2 ** attempt)
    raise last


def _token(host, path):
    if host == "ghcr.io":
        return json.loads(_open(f"https://ghcr.io/token?scope=repository:{path}:pull"))["token"]
    if host == "registry-1.docker.io":
        query = urllib.parse.urlencode(
            {"service": "registry.docker.io", "scope": f"repository:{path}:pull"}
        )
        return json.loads(_open(f"https://auth.docker.io/token?{query}"))["token"]
    return None


def _registry_get(host, path, suffix):
    headers = {"Accept": ACCEPT}
    token = _token(host, path)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return _open(f"https://{host}/v2/{path}/{suffix}", headers)


def registry(name):
    if name.startswith("harbor.homelab.pastelariadev.com/"):
        project, _, path = name.split("/", 1)[1].partition("/")
        host = HARBOR_HOSTS.get(project)
        if not host:
            return None, None
        return ("registry-1.docker.io" if host == "docker.io" else host), path
    first, _, path = name.partition("/")
    if "." in first or ":" in first or first == "localhost":
        return ("registry-1.docker.io" if first == "docker.io" else first), path
    return "registry-1.docker.io", f"library/{path}" if "/" not in name else name


def image_created(ref):
    name, tag, digest = split_ref(ref)
    host, path = registry(name)
    if host is None:
        return None
    body = _registry_get(host, path, f"manifests/{tag or digest}")
    resolved = "sha256:" + hashlib.sha256(body).hexdigest()
    if digest and digest != resolved:
        raise ValueError("tag no longer resolves to the pinned digest")
    manifest = json.loads(body)
    if "manifests" in manifest:
        children = [
            entry for entry in manifest["manifests"]
            if (entry.get("platform") or {}).get("architecture") == "amd64"
            and (entry.get("platform") or {}).get("os") == "linux"
        ]
        if not children:
            raise ValueError("no linux/amd64 manifest")
        manifest = json.loads(_registry_get(host, path, f"manifests/{children[0]['digest']}"))
    config = json.loads(_registry_get(host, path, f"blobs/{manifest['config']['digest']}"))
    return config.get("created")


def parse_created(value):
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    match = re.match(r"^(.*?)\.(\d+)(.*)$", text)
    if match:
        text = f"{match.group(1)}.{match.group(2)[:6].ljust(6, '0')}{match.group(3)}"
    return datetime.fromisoformat(text)


def age_violations(base):
    now = datetime.now(timezone.utc)
    digests = changed_digests(base)
    rendered, plain = touched_units(base)
    violations = []
    for unit in rendered:
        for ref in unit.refs():
            if split_ref(ref)[2] not in digests:
                continue
            violations.extend(age_check(ref, f"{unit.path}: {ref}", now))
    for name in plain:
        text = (ROOT / name).read_text(encoding="utf-8")
        for document in yaml.safe_load_all(text) if yaml else []:
            for ref in image_refs(document):
                if split_ref(ref)[2] not in digests:
                    continue
                violations.extend(age_check(ref, f"{name}: {ref}", now))
    return violations


def age_check(ref, where, now):
    name, _, digest = split_ref(ref)
    if registry(name)[0] is None:
        # Built and published here (Harbor-managed project); no upstream tag to age.
        return []
    if not DIGEST_RE.match(digest or ""):
        return [f"{where}: not a pinned ref, age unverifiable"]
    try:
        created = parse_created(image_created(ref))
    except Exception as error:  # noqa: BLE001 - keep scanning other refs
        return [f"{where}: cannot resolve image ({error})"]
    if created is None:
        return [f"{where}: image has no created timestamp"]
    age = now - created
    if age < MIN_AGE:
        return [
            f"{where}: {age.total_seconds() / 3600:.1f}h old (minimum {MIN_AGE.days}d)"
        ]
    return []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--age", action="store_true", help="check image age instead of ref shape")
    parser.add_argument(
        "--base",
        help="git revision to diff against. Policy mode checks only touched components; "
        "without it, the whole repository is audited.",
    )
    args = parser.parse_args(argv)

    if args.age:
        if not args.base:
            parser.error("--age requires --base")
        violations, subject = age_violations(args.base), "image age"
    else:
        violations, subject = policy_violations(args.base), "image refs"

    if violations:
        print(f"{subject} policy failed:", file=sys.stderr)
        for line in dict.fromkeys(violations):
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"{subject} policy passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
