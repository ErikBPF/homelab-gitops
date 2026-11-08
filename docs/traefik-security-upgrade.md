# Traefik security upgrade and CRD authority

**Status:** source prepared; live sync and schema verification pending.

Chart 42.3.1 and Traefik v3.7.17 are pinned together. The main chart's bundled
CRDs are the only source for both `traefik.io` and `hub.traefik.io`. The separate
`traefik-crds` dependency is removed, and the Argo Application explicitly keeps
`helm.skipCrds: false`. This removes ten conflicting duplicate definitions while
retaining all ten Traefik and fifteen Hub CRDs.

The bundled schema includes additive v3.8-generation fields even though this
chart's appVersion is v3.7.17. Do not infer controller support for those fields
from schema admission alone. This upgrade does not start using them.

The previous chart rendered the front-door Service as LoadBalancer because its
old service.type value was inert. The new service.spec.type makes it NodePort.
Treat this as a real Service-type correction in the named maintenance window.
Ports remain web 30081 and websecure 30444; metrics remains ClusterIP on 9100.
The running Service type has not been observed in this task.

## Before and after the authorized sync

1. Wait until the real three-day image-age gate passes, no earlier than
   2026-11-08 19:11:35 UTC. Keep the PR draft until the rollout window is agreed.
2. From this source revision, run `helm dependency build platform/traefik`, then
   `helm template traefik platform/traefik --include-crds`. The release contract
   requires exactly one definition per CRD name and all 25 required CRDs.
3. Review the Argo desired/live diff for every CRD before sync. The main chart is
   authoritative; do not separately apply the retired subchart's definitions.
4. After sync, use the existing `homelab` context to inspect each applied CRD,
   for example `kubectl --context homelab get crd ingressroutes.traefik.io -o json`.
   Compare each rendered `spec.versions[].schema.openAPIV3Schema` with its applied
   counterpart for both groups. Record the source revision, 25 matches and no
   duplicate names. Any mismatch blocks acceptance.
5. Verify normal ingress routing, the L4 front door and separate metrics Service.
   No flood or load test is needed. Keep the last reviewed source and its schema
   diff available for recovery; do not delete CRDs to reverse this upgrade.

Local render checks establish desired state only. No sync or live schema audit
has been performed by this source change.

## Ingress address publication

Traefik explicitly publishes the existing Kepler L4 ingress VIP
`192.168.10.250` into managed Ingress status. Chart 42.3.1 defaults
`providers.kubernetesIngress.publishedService.enabled` to `true`; this deployment
explicitly sets it to `false` so `ingressEndpoint.ip` takes effect.

In the pinned Traefik v3.7.17 implementation, the `ServiceTypeNodePort` branch of
`updateIngressStatus` reads node addresses and selects `NodeExternalIP`. These
nodes have only InternalIPs, so that branch produces no addresses. Copying the
front Service's `status.loadBalancer.ingress` belongs to the separate
`ServiceTypeLoadBalancer` branch. The explicit VIP restores Argo CD's Ingress
health assessment without changing routing, NodePorts, or health-check policy.

References: [chart defaults](https://github.com/traefik/traefik-helm-chart/blob/v42.3.1/traefik/values.yaml),
[pinned status implementation](https://github.com/traefik/traefik/blob/v3.7.17/pkg/provider/kubernetes/ingress/kubernetes.go#L444-L546).
