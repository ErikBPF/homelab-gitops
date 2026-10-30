# Disposable Alloy permission, metrics and TLS proof

The aligned image passed both local replica runs on 2026-10-30. The executed
binary was **Alloy v1.21.2**, revision `a69b1ae`, matching chart 1.14.3's declared
appVersion. Both two-node guest pairs stopped successfully. The production source
change is only the image digest; no production deployment or Harbor access was
performed. Harbor availability remains a prerequisite before landing or deploying
that pin.

A passed in 190.25 seconds with run ID
`d06e42b5-1046-460b-86bc-55a4e6e7f4f3`. Four real direct reads—Secret,
ConfigMap, pod log and an unrelated-namespace Service—returned HTTP 200 with known
synthetic contents under legacy grants. Applying scoped RBAC without pruning kept
that access. Removing the exact former role and binding changed all four to
Kubernetes Forbidden 403. Allowed node reads and Service discovery in `traefik`
and `argocd` continued through the actual ServiceAccount identity.

A fresh receiver observed cAdvisor CPU/memory and kubelet identity/pod-count
series from both nodes, plus synthetic Traefik and Argo discovery series. It had
no previous samples or independent scrape jobs; the unique run label bound the
samples to this execution.

B passed in 299.06 seconds with run ID
`b617aa41-2f72-43f8-a629-a93d7134d6f5`. The same image executed both actual
scrape blocks against six local TLS witnesses. A synthetic token with no
Kubernetes privilege occupied the existing token path. Trusted, wrong-CA and
wrong-name certificates exercised each scrape separately.

| Phase | Trusted endpoints | Four wrong-certificate endpoints | Fresh endpoint samples |
|---|---|---|---|
| Secure, 60.03s | Authenticated HTTP and metrics | TLS attempts and Alloy x509 errors; zero HTTP/authentication | 4 |
| Insecure mutation, 44.63s | Authenticated HTTP and metrics | Authenticated HTTP and metrics; denial validator failed | 12 |
| Restored, 68.71s | Authenticated HTTP and metrics | TLS attempts and Alloy x509 errors; zero HTTP/authentication | 4 |

These are observed witness snapshots and receiver samples. Each transition
terminated the prior pod, restarted witnesses and reset counters; distinct phase
labels and fresh Alloy storage prevented reuse. Administrative readiness used a
separate listener. Actual target URLs, health, scrape times and certificate errors
were retained with selected metric labels and values. Readiness, silence,
`up=0`, timeouts or missing targets cannot establish rejection.

## Reproduce locally

[The artifact manifest](../tests/replica/artifacts.json) pins chart, image index,
Linux/amd64 manifest, configuration, archive and expected binary version. The
published Desktop substrate is revision
`bdfe7e2ef605a2988873d6a2694498128fe569da`; builds never read a sibling checkout.
The selected image manifest is
`sha256:2fc2ae17ed3b712e37047724efd1cdf7a4124016c0b7522ac469dab0ddf8a8d7`.
The deterministic archive SHA-256 is
`3570ce6b7c660cdf6988f57c4abb7044faef46f52e09f7cd65225a4cd130b4a7`.

Use x86_64 Linux, accessible KVM, a local Nix daemon supporting `kvm` and
`nixos-test`, Python 3.11+, Bash, coreutils and `just`. Each run uses two guests,
each with two vCPUs, 2048 MiB RAM and an 8 GiB disk.

```sh
image_root=$(mktemp -d -t alloy-replica-image.XXXXXXXX)
python3 tests/replica/acquire_image.py "$image_root/archive"
bash tests/replica/run.sh --dry-run "$image_root/archive/alloy-linux-amd64.oci.tar"
timeout --signal=TERM --kill-after=60s 2700s \
  just test-alloy-replica "$image_root/archive/alloy-linux-amd64.oci.tar"
timeout --signal=TERM --kill-after=60s 2700s \
  just test-alloy-tls-replica "$image_root/archive/alloy-linux-amd64.oci.tar"
```

Acquisition is anonymous and verifies every pinned blob/platform mapping, with no
host registry credentials or Harbor fallback. The wrapper clears inherited
environment, selects only the public Nix cache, disables remote builders and
bounds local jobs. Guests restrict external networking and expose no forwarded
ports. Images resolve through CRI on both nodes and use pull policy `Never`.
Each UUID changes the derivation; final test results cannot be substituted.
Native runs are bounded at 1800 seconds, outer commands at 2700 seconds and B
phases at 180 seconds. Success requires both guests to stop.

## Scope and recovery

`just test` includes fifteen payload, seventeen TLS fixture and eleven API-helper
tests; it starts no VMs. The [Gherkin contract](../tests/replica/alloy-security.feature)
remains unautomated; native runners implement its behavior procedurally.
The earlier R21 v1.18.1 proof remains historical in the predecessor change.

`nodes/proxy` remains powerful: direct API denials do not establish denial of
indirect kubelet operations. Synthetic endpoints do not prove deployed certificate,
exporter, PVC, KSM or etcd correctness. Reverting this follow-up restores the prior
source image pin and fixture behavior. Production TLS verification was unchanged.
