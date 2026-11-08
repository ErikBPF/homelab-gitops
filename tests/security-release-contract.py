#!/usr/bin/env python3
"""Check rendered workload pins against the reviewed October/November fixes."""

import argparse
import importlib.util
from pathlib import Path
import re
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("image_policy", ROOT / "scripts/check-image-policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

# Advisory floors, not a claim that future releases have no vulnerabilities.
# Register a service only in the slice that patches it; --all covers active entries.
COMPONENTS = {
    "argocd": ("argocd", "/argoproj/argocd", (3, 5, 4)),
    "authentik": ("platform/authentik", "/goauthentik/server", (2026, 8, 2)),
    "traefik": ("platform/traefik", "/library/traefik", (3, 7, 17)),
    "openwebui": ("apps/homelab/openwebui", "/open-webui/open-webui", (0, 12, 2)),
    "litellm": ("apps/homelab/litellm", "/berriai/litellm-database", (1, 106, 5)),
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
        and not (component == "litellm" and version == (1, 107, 0))
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true")
    selection.add_argument("--component", choices=COMPONENTS, action="append")
    args = parser.parse_args()
    components = list(COMPONENTS) if args.all else args.component
    old = {"argocd": "3.5.1", "authentik": "2026.5.6", "traefik": "3.7.16", "openwebui": "0.11.3", "litellm": "1.100.0"}
    for component in components:
        directory, suffix, minimum = COMPONENTS[component]
        prefix = "registry.example" + suffix + ":"
        digest = "@sha256:" + "1" * 64
        assert not reviewed(component, prefix + old[component] + digest)
        assert reviewed(component, prefix + ".".join(map(str, minimum)) + digest)
        assert not reviewed(component, prefix + "latest" + digest)
        assert not reviewed(component, prefix + ".".join(map(str, minimum)))
        if component == "litellm":
            assert not reviewed(component, prefix + "1.107.0" + digest), "compromised release must stay rejected"
        documents = [doc for doc in yaml.safe_load_all(policy.Unit("helm", directory).text()) if doc]
        if component == "traefik":
            services = [doc for doc in documents if doc.get("kind") == "Service"]
            front = next(doc["spec"] for doc in services if doc["metadata"]["name"] == "traefik")
            assert front["type"] == "NodePort", "Traefik must retain its L4 front door"
            ports = {port["name"]: port.get("nodePort") for port in front["ports"]}
            assert ports.get("web") == 30081 and ports.get("websecure") == 30444, ports
            metrics = next(doc["spec"] for doc in services if doc["metadata"]["name"] == "traefik-metrics")
            assert metrics["type"] == "ClusterIP" and metrics["ports"][0]["port"] == 9100
            # Unit.text() above initializes chart dependencies even on a fresh checkout.
            rendered_crds = subprocess.check_output(
                ["helm", "template", "traefik", directory, "--include-crds"], cwd=ROOT, text=True,
            )
            crds = [doc for doc in yaml.safe_load_all(rendered_crds)
                    if doc and doc.get("kind") == "CustomResourceDefinition"]
            names = [doc["metadata"]["name"] for doc in crds]
            assert len(names) == len(set(names)), "duplicate CRD authorities in rendered desired state"
            assert {group: sum(doc["spec"]["group"] == group for doc in crds)
                    for group in ("traefik.io", "hub.traefik.io")} == {"traefik.io": 10, "hub.traefik.io": 15}
            app = yaml.safe_load((ROOT / "clusters/kepler/apps/traefik.yaml").read_text())
            assert app["spec"]["source"].get("helm", {}).get("skipCrds") is False
        refs = [ref for doc in documents for ref in policy.image_refs(doc)
                if policy.split_ref(ref)[0].endswith(suffix)]
        assert refs, f"{component}: target image missing from rendered workloads"
        for ref in refs:
            assert reviewed(component, ref), f"{component}: affected, compromised or unpinned artifact: {ref}"
    print("security release contract OK: " + ", ".join(components))


if __name__ == "__main__":
    main()
