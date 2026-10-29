#!/usr/bin/env python3
"""Check the permissions granted by the rendered metrics-scraper chart."""

import pathlib
import subprocess

import yaml


root = pathlib.Path(__file__).resolve().parents[1]
rendered = subprocess.check_output(
    ["helm", "template", "alloy-metrics", "platform/alloy-metrics", "--namespace", "monitoring"],
    cwd=root,
    text=True,
)
objects = [obj for obj in yaml.safe_load_all(rendered) if obj]
account = "alloy-metrics"
namespace = "monitoring"
roles = {
    (obj["kind"], obj["metadata"].get("namespace", namespace) if obj["kind"] == "Role" else "", obj["metadata"]["name"]): obj
    for obj in objects
    if obj["kind"] in {"Role", "ClusterRole"}
}
grants = set()
for binding in objects:
    if binding["kind"] not in {"RoleBinding", "ClusterRoleBinding"}:
        continue
    scope = binding["metadata"].get("namespace", namespace) if binding["kind"] == "RoleBinding" else "cluster"
    subjects = binding.get("subjects", [])
    matches = any(
        (s["kind"] == "ServiceAccount" and s["name"] == account and s.get("namespace", scope) == namespace)
        or (s["kind"] == "User" and s["name"] == f"system:serviceaccount:{namespace}:{account}")
        or (s["kind"] == "Group" and s["name"] in {"system:authenticated", "system:serviceaccounts", f"system:serviceaccounts:{namespace}"})
        for s in subjects
    )
    if not matches:
        continue
    ref = binding["roleRef"]
    assert ref["apiGroup"] == "rbac.authorization.k8s.io", ref
    role = roles[(ref["kind"], scope if ref["kind"] == "Role" else "", ref["name"])]
    assert not role.get("aggregationRule"), "aggregated permissions require explicit review"
    for rule in role.get("rules", []):
        assert not rule.get("nonResourceURLs"), rule
        assert not rule.get("resourceNames"), "discovery needs complete list/watch access"
        for group in rule["apiGroups"]:
            for resource in rule["resources"]:
                for verb in rule["verbs"]:
                    grants.add((scope, group, resource, verb))

expected = {
    ("cluster", "", resource, verb)
    for resource in ("nodes", "nodes/metrics", "nodes/proxy")
    for verb in ("get", "list", "watch")
} | {
    (scope, "", "services", verb)
    for scope in ("traefik", "argocd")
    for verb in ("get", "list", "watch")
}
assert grants == expected, f"unexpected grants: {sorted(grants - expected)}; missing grants: {sorted(expected - grants)}"
deployments = [obj for obj in objects if obj["kind"] == "Deployment"]
assert len(deployments) == 1, "expected one metrics scraper"
assert deployments[0]["spec"]["template"]["spec"]["serviceAccountName"] == account
assert deployments[0]["metadata"].get("namespace", namespace) == namespace
assert any(obj["kind"] == "ServiceAccount" and obj["metadata"]["name"] == account
           and obj["metadata"].get("namespace", namespace) == namespace for obj in objects)
print("ALLOY_RENDERED_RBAC_OK")
