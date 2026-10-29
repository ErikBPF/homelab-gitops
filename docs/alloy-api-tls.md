# Alloy API-server TLS verification

The `cadvisor` and `kubelet` scrapes authenticate to the Kubernetes API-server
proxy with a service-account bearer token. They must verify the server before
sending that token. Both now trust the projected service-account CA at
`/var/run/secrets/kubernetes.io/serviceaccount/ca.crt` and require the certificate
identity `kubernetes.default.svc`. The previous configuration disabled server
certificate verification.

This change preserves the discovery targets, proxy paths, token file, scrape
intervals, metric filtering, forwarding, and existing `nodes/proxy` permission.
That permission remains a separate hardening concern: it must not be removed
until an alternative scrape path has demonstrated both useful metrics and the
intended authorization boundary.

## Verification

Run the existing source contract from the repository root:

```sh
bash tests/alloy-resilience.sh
```

The Python used by this contract needs PyYAML, already installed by the validation
workflow. The contract checks each scrape separately for its CA path and expected
server name, and rejects disabled verification. It remains part of
`scripts/validate.sh` through the existing Alloy resilience contract. It is a
source regression check, not proof of a running cluster's certificate or mounts.

The 2026-10-29 implementation observed an assertion failure against the original
configuration before changing either scrape. The updated contract and local
Alloy 1.17.1 validation of the complete configuration passed. Disposable loopback
checks use the same scrape TLS settings, synthetic CA and token files, and static
local targets; these checks do not exercise Kubernetes discovery, the API proxy's
authorization, remote write, or the deployed image. Their receipts are retained
with the hardening review rather than embedding generated keys in this repository.
The original settings accepted wrong-CA and wrong-name endpoints for both scrapes.
The updated settings accepted the trusted matching endpoint and rejected both
negative controls before any HTTP authentication reached the endpoint.

The first render attempt lacked the exact dependency. A later isolated download
of the declared Alloy chart 1.13.1 closed that focused gap: the candidate rendered,
all eight resources passed strict schema validation, both TLS blocks survived,
and service-account token mounting remained enabled. The chart declares appVersion
v1.20.1; the local loopback test used Alloy 1.17.1. This is not execution of the
rendered image or proof of its deployed digest. The subsequent full repository
validator passed on the frozen candidate: 22 chart renders, six Kustomizations,
combined manifests and 37 contracts. Schema stages recorded 515 valid and 175
skipped validation events; missing schemas remain a coverage gap. The six later
advisory-scanner paths passed their thirteen focused tests and syntax checks.
This composed result does not claim a full run of that later revision or of
newer chart versions arriving on the merge target.

## Deployment and recovery

Before rollout, verify that the rendered pod retains its service-account token
and CA projection. In an authorized disposable cluster, confirm that the serving
certificate chains to that CA and covers `kubernetes.default.svc`, and that both
scrapes deliver their expected series. A successful readiness probe alone does
not establish successful scraping. Wrong-CA and wrong-name endpoints must fail
before receiving an HTTP Authorization header.

After an authorized rollout, inspect scrape errors and the expected cAdvisor and
kubelet series. If verification fails, correct the API-server certificate chain,
name, or projected CA instead of permanently disabling verification. Reverting
the two TLS blocks restores the old behavior but reopens credential-interception
risk; any emergency rollback needs an explicit, time-bounded exception. No
cluster access, deployment, or credential collection was part of this source fix.

The TLS fields and API-server authentication pattern follow the
[official Alloy scrape reference](https://grafana.com/docs/alloy/latest/reference/components/prometheus/prometheus.scrape/#tls_config).
