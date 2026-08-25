#!/usr/bin/env bash
set -euo pipefail

if grep -R -n -E '^[[:space:]]+automated:' bootstrap clusters; then
  echo "automated Argo sync is forbidden while main is unprotected" >&2
  exit 1
fi

if grep -nE '^[[:space:]]+(directory:|recurse:)' bootstrap/root-app.yaml; then
  echo "recursive root discovery is forbidden while main is unprotected" >&2
  exit 1
fi

python - clusters/kepler/apps <<'PY'
import pathlib
import sys

import yaml

root = pathlib.Path(sys.argv[1])
listed = set(yaml.safe_load((root / "kustomization.yaml").read_text())["resources"])
expected = {path.name for path in root.glob("*.yaml")} - {"kustomization.yaml"}
assert listed == expected, (sorted(expected - listed), sorted(listed - expected))
PY

test "$(yq -r '.spec.project' bootstrap/root-app.yaml)" = platform
grep -Fq 'kubectl --context {{ctx}} apply -f clusters/kepler/apps/projects.yaml' justfile

grep -Fq 'just sync "$SHA" root' README.md
grep -Fq 'just sync "$SHA" "$app"' README.md
grep -Fq 'sync revision app="root":' justfile
grep -Fq 'scripts/sync.sh {{quote(revision)}} {{quote(app)}}' justfile

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cat >"$tmp/argocd" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >"$ARGOCALL"
SH
mv "$tmp/argocd" "$tmp/kubectl"
chmod +x "$tmp/kubectl"

if PATH="$tmp:$PATH" ARGOCALL="$tmp/call" scripts/sync.sh main demo >/dev/null 2>&1; then
  echo "mutable revision unexpectedly accepted" >&2
  exit 1
fi
test ! -e "$tmp/call"

sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
if PATH="$tmp:$PATH" ARGOCALL="$tmp/call" scripts/sync.sh "$sha" 'demo;bad' >/dev/null 2>&1; then
  echo "invalid application name unexpectedly accepted" >&2
  exit 1
fi
test ! -e "$tmp/call"

PATH="$tmp:$PATH" ARGOCALL="$tmp/call" scripts/sync.sh "$sha" demo >/dev/null
test "$(cat "$tmp/call")" = "--context homelab -n argocd exec statefulset/argocd-application-controller -- argocd app sync demo --core --revision $sha"

PATH="$tmp:$PATH" ARGOCALL="$tmp/call" scripts/retire-vcluster-pastelariadev.sh >/dev/null
test "$(cat "$tmp/call")" = "--context homelab -n argocd exec statefulset/argocd-application-controller -- argocd app delete vcluster-pastelariadev --core --cascade --yes"
