# Alloy metrics discovery permissions

The metrics scraper discovers nodes and Services in `traefik` and `argocd`.
Its chart creates service-only Roles and RoleBindings in those two namespaces,
instead of inheriting chart defaults for cluster-wide Secret, ConfigMap, pod-log
and other API reads. The Deployment uses `monitoring/alloy-metrics`.

The separate `alloy-metrics-nodes-proxy` ClusterRole still grants node discovery
and the existing API proxy scrape. The scraper configuration, API CA, server
name, targets and labels are unchanged. This is a reduction of chart permissions,
not completion of metrics-only authorization: `nodes/proxy` still permits
powerful indirect access. Removing it requires a disposable compatibility test
for direct kubelet metrics, certificate validation and the required series.

After `helm dependency build platform/alloy-metrics`, run
`python3 tests/alloy-rbac.py` or the existing
`bash tests/alloy-resilience.sh`. The owner validator runs the latter. The new
contract renders the actual chart with namespace `monitoring`, checks the roles
bound to the scraper identity and requires exactly the retained node/proxy grants
plus service discovery in the two namespaces. Its success marker is
`ALLOY_RENDERED_RBAC_OK`. This is rendered-source validation; it does not query a
cluster or prove effective live permissions or successful ingestion.

Rollout must remove the former chart ClusterRole and ClusterRoleBinding as well
as create the scoped roles. An apply without pruning can leave the previous
cluster-wide grants active. The existing platform AppProject permits the needed
namespaces and RBAC kinds. Verify effective grants and healthy discovery/metrics
after the separately authorized sync, including any roles managed outside this
chart. If a required reader was missed, restore only the specific documented
grant while retaining verified TLS. Do not silently restore unrestricted chart
defaults as a fallback.

The configured roles follow [Alloy service and node discovery](https://grafana.com/docs/alloy/latest/reference/components/discovery/discovery.kubernetes/).
