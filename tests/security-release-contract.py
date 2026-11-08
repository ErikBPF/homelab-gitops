#!/usr/bin/env python3
"""Check rendered workload pins against the reviewed October/November fixes."""

import argparse
import importlib.util
from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("image_policy", ROOT / "scripts/check-image-policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

# Advisory floors, not a claim that future releases have no vulnerabilities.
# Register a service only in the slice that patches it; --all covers active entries.
COMPONENTS = {
    "argocd": ("argocd", "/argoproj/argocd", (3, 5, 4)),
}


def reviewed(component, ref):
    name, tag, digest = policy.split_ref(ref)
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", tag or "")
    if not match or not policy.DIGEST_RE.fullmatch(digest or "") or digest == policy.ZERO_DIGEST:
        return False
    version = tuple(map(int, match.groups()))
    return (
        name.endswith(COMPONENTS[component][1])
        and version >= COMPONENTS[component][2]
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true")
    selection.add_argument("--component", choices=COMPONENTS, action="append")
    args = parser.parse_args()
    components = list(COMPONENTS) if args.all else args.component
    old = {"argocd": "3.5.1"}
    for component in components:
        directory, suffix, minimum = COMPONENTS[component]
        prefix = "registry.example" + suffix + ":"
        digest = "@sha256:" + "1" * 64
        assert not reviewed(component, prefix + old[component] + digest)
        assert reviewed(component, prefix + ".".join(map(str, minimum)) + digest)
        assert not reviewed(component, prefix + "latest" + digest)
        assert not reviewed(component, prefix + ".".join(map(str, minimum)))
        documents = [doc for doc in yaml.safe_load_all(policy.Unit("helm", directory).text()) if doc]
        refs = [ref for doc in documents for ref in policy.image_refs(doc)
                if policy.split_ref(ref)[0].endswith(suffix)]
        assert refs, f"{component}: target image missing from rendered workloads"
        for ref in refs:
            assert reviewed(component, ref), f"{component}: affected, compromised or unpinned artifact: {ref}"
    print("security release contract OK: " + ", ".join(components))


if __name__ == "__main__":
    main()
