#!/usr/bin/env bash
set -euo pipefail

# Argo syncs `main` automatically. That is only safe while main is protected:
# every change lands through a reviewed PR whose required `kubeconform` check
# passed. This contract keeps the automation, its precondition, and the manual
# exact-revision escape hatch coherent.

# 1. Root app-of-apps and every child Application are automated.
grep -Eq '^[[:space:]]+automated:' bootstrap/root-app.yaml
grep -Fq 'automated:' clusters/kepler/apps/kustomization.yaml
grep -Fq 'prune: true' clusters/kepler/apps/kustomization.yaml
grep -Fq 'selfHeal: true' clusters/kepler/apps/kustomization.yaml

# 2. The required check must run on every pull request, including docs-only
#    ones, or protected main would deadlock on a check that never reports.
! grep -q 'paths-ignore' .github/workflows/validate.yml

# 3. Manual exact-revision sync stays documented as the rollback/escape hatch.
grep -Fq 'just sync "$SHA" root' README.md
grep -Fq 'just sync "$SHA" "$app"' README.md
grep -Eq '^sync[[:space:]]' justfile

# 4. Child applications stay an explicit list (no recursive discovery).
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
