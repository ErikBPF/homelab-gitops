# Disposable Alloy permission and metrics proof

Slice A passed on 2026-10-30 with run ID
`e8ec8c78-1d41-47e2-8f4f-5068b7a6e377`. A two-node disposable Kubernetes replica ran the pinned Alloy scraper and
delivered the required fresh series; both guests stopped successfully. Production configuration and image pins were unchanged.

The native test observed four direct API reads—Secret, ConfigMap, pod log and a
Service in an unrelated namespace—return HTTP 200 with expected synthetic
contents under the former broad grants. Applying the scoped chart without pruning
retained that access. Removing the exact former ClusterRole and binding changed
all four reads to Kubernetes Forbidden HTTP 403. Node reads and Service discovery
in `traefik` and `argocd` continued to work through the actual ServiceAccount
identity, including its normal authentication groups.

The isolated receiver observed `container_cpu_usage_seconds_total`,
`container_memory_working_set_bytes`, `kubelet_node_name` and
`kubelet_running_pods` from both nodes. Synthetic Traefik and Argo series also
arrived through their configured discovery routes. The receiver had an empty data
directory and no independent scrape jobs; its unique cluster label bound the
samples to this run. Readiness alone cannot pass these assertions.

## Pinned inputs and version distinction

[The artifact manifest](../tests/replica/artifacts.json) records the full chart,
image index, Linux/amd64 manifest, configuration and deterministic archive hashes.
The substrate is the published Desktop revision
`bdfe7e2ef605a2988873d6a2694498128fe569da`; builds never read another checkout.

| Input | Verified value |
|---|---|
| Helm chart | Alloy 1.14.3, declaring appVersion v1.21.2 |
| Executed image | Alloy **v1.18.1**, revision `6012ec4`, Linux/amd64 |
| Runtime repository digest | `docker.io/grafana/alloy@sha256:754409730f1a4ed9781f8a2ea3b6a8c55750ee125a267ecf8fb449f9a25c109a` |
| OCI archive SHA-256 | `9e20208ef7ba49a1aa9e770251b71da77769fff9129cefdd2fbd73a509e9a241` |

The chart's declared app version is not the observed binary version. This
version-alignment difference remains a separate compatibility follow-up; the
fixture retains the configured image. Both nodes must resolve its exact
repository digest and configuration digest through CRI before any canary starts.
The running Alloy pod's image identity is checked again.

## Reproduce locally

Use x86_64 Linux with accessible KVM, a local Nix daemon supporting the `kvm` and
`nixos-test` features, Python 3.11+, Bash, coreutils and `just`. The published
substrate declares two guests, each with two vCPUs, 2048 MiB RAM and an 8 GiB disk.
Closure downloads and assembly depend on the local cache.

```sh
image_root=$(mktemp -d -t alloy-replica-image.XXXXXXXX)
python3 tests/replica/acquire_image.py "$image_root/archive"
bash tests/replica/run.sh --dry-run "$image_root/archive/alloy-linux-amd64.oci.tar"
timeout --signal=TERM --kill-after=60s 2700s \
  just test-alloy-replica "$image_root/archive/alloy-linux-amd64.oci.tar"
```

Acquisition uses anonymous public requests, validates every pinned blob and
platform mapping, and creates the exact deterministic archive. Existing output
directories are rejected. No host registry authentication or Harbor fallback is
used. The wrapper verifies the archive, copies it into the local Nix store and
creates a fresh UUID for each invocation.

The wrapper clears the environment and HOME, selects only the public Nix cache,
disables flake configuration and remote builders, and bounds local jobs/cores.
Both guests restrict external networking and expose no forwarded ports. Images
are imported locally and use pull policy `Never`. The final test result cannot be
substituted from a cache. Native execution has a 1800-second limit; the command
above adds a 45-minute outer bound. Both shutdowns are attempted on failure, and
the success marker requires completed assertions and cleanup. The wrapper prints
the resulting `alloy-replica.json` path.

## Scope and remaining proof

The renderer preserves both actual cAdvisor and kubelet scrape blocks and rejects
unknown configuration, resources and complete-manifest changes before boot. It
removes production KSM/etcd targets and substitutes the local receiver and run
label. Runtime credentials exist only inside the disposable guests.

`nodes/proxy` remains allowed and powerful. Direct API denial does not prove
that indirect kubelet operations are denied. Synthetic targets do not establish
production Traefik/Argo compatibility, PVC metrics, KSM or etcd behavior. Slice B's
same-image wrong-CA and wrong-name matrix remains planned.

`just test` includes eleven payload tests and eleven native TLS API-helper tests;
it does not start VMs. The [Gherkin contract](../tests/replica/alloy-security.feature)
remains unautomated: the Nix runner implements A procedurally. Reverting this
test-only change requires no production rollout.
