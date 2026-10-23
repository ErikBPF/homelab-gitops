# homelab-gitops

> **SSOT role:** owns **non-media home-services and lab / prod-mimic Kubernetes
> workloads** (Argo CD) on the Kepler k3s cluster. Consumes the k3s substrate
> from desktop-nixos + secrets via ESO→OpenBao@Discovery.

Production-mimicking platform layer for the stable **kepler** k3s cluster
(`homelab`), deployed via **Argo CD** (GitOps). A sister repo of
`desktop-nixos`, on the **servarr strategy**: a git repo + `just` recipes,
applied to the cluster — **not** baked into the NixOS flake.

See the implemented record:
`desktop-nixos/docs/implemented/2026-06-20-cluster-homelab-gitops.md`.

## Model

- **The NixOS flake owns the substrate** (k3s, networking/NAT, the L4 LB, PSA
  admission, etcd snapshots, bootstrap credentials, and their reconciler).
  **This repo owns the platform/workload layer on top**, including Traefik and
  in-cluster telemetry collectors. Disjoint objects — no controller fights.
- **Argo CD is bootstrapped once** by `just bootstrap` (helm install +
  `kubectl apply` the root app-of-apps), then pulls everything from this repo,
  including its own chart ("Argo manages Argo"). Root and child applications
  stay manual while `main` is unprotected.

## Layout

```
bootstrap/root-app.yaml      app-of-apps; kubectl apply'd by `just bootstrap`
argocd/                      Argo CD self-management (wrapper chart)
clusters/kepler/apps/        explicit Kustomization, AppProjects, and Applications
platform/<component>/        thin Helm wrapper chart: Chart.yaml (1 dep) + values.yaml
apps/homelab/<workload>/     lab, automation, and AI workloads
apps/home-services/<service> household-facing non-media workloads
```

Wrapper-chart convention (Chart.yaml = one pinned dependency + values.yaml)
mirrors the `datahomelab-gitops` reference — Renovate-friendly.

## Dependency updates

Renovate keeps Helm charts, GitHub Actions and Nix flake inputs current, but it
does **not** bump the Harbor image pins in `platform/*/values.yaml`: the
registry is not resolvable from GitHub runners, so the shared preset disables
those dependencies. A wrapper's chart version and its pinned image therefore
have to move together — chart 4.13.5 shipped flags that the pinned v4.13.4
binary rejected and crash-looped the CSI controller after auto-sync deployed it.

`tests/chart-image-parity.py` enforces that invariant for wrappers that pin an
image (currently `platform/csi-driver-nfs`); a chart bump without the matching
image pin fails CI. Add a pairing there when a new wrapper pins an image.

## Bootstrap

```
just bootstrap     # helm install argocd + apply root app-of-apps
just status        # argocd app list
just ui            # how to reach the Argo UI
```

Argo CD syncs `main` automatically. Every change lands through a reviewed pull
request whose required `kubeconform` check passed, so the merged revision is the
deployed revision; `prune` removes resources deleted from git and `selfHeal`
reverts out-of-band edits. Pull requests change only the intent — watch the app
in Argo (or `just status`) for the sync result and health.

Manual exact-revision sync remains the escape hatch for rollback and one-time
operations. Sync the root first, then the affected child, and check each
operation result, revision, and health before the next:

```bash
SHA="$(git rev-parse HEAD)" # prior known-good commit for a rollback
just sync "$SHA" root
```

```sh
app=jaeger # example; choose the application being updated
just sync "$SHA" "$app"
```

`just sync` submits an operation through the Kubernetes API using your selected
context; it does not wait for completion. An active operation is rejected; a
resource-version precondition prevents overwriting a concurrent request.

Uses your existing kubeconfig (context `homelab`). The Argo UI is exposed
via the cluster ingress behind the kepler L4 LB (no LoadBalancer Service).

## Implemented prerequisites

- **external-secrets** → **OpenBao on Discovery** (host service owned by
  `desktop-nixos`, Vault-compatible API, dedicated AppRole)
  + the `vault-approle` bootstrap Secret reconciled from desktop-nixos sops +
  an ACL grant `kepler -> discovery:8200` (homelab-iac). Wave -3.
- **csi-driver-nfs** (`nfs-fast` / `nfs-slow`) → kepler must NFS-export an SSD
  dataset and `/bulk` to the cluster subnet `10.250.0.0/24`. Implemented. Wave -2.
- **kube-state-metrics** → scraped by `alloy-metrics` into the in-cluster
  Prometheus backend. The legacy Discovery backend remains active only until
  the remaining host collectors and external dead-man gate pass.

## Current applications

The root app manages 26 child applications (27 Argo Applications including
`root`):

- `argocd`, `traefik`, `csi-driver-nfs`, `external-secrets`, `cert-manager`,
  `clickhouse-operator`, `kube-state-metrics`, `alloy-metrics`, `keda`, `jaeger`,
  `otel-collector`, `monitoring`, `loki`,
  `scrutiny`, `wazuh`, `trace-demo`, `demo`, `airflow`, `langfuse`,
  `changedetection`, `karakeep`, `immich`, and the `cognee` graph+RAG experiment.
- Traefik is the default IngressClass behind Kepler's `.250` L4 endpoint.
- ESO reads runtime secrets from OpenBao@Discovery through a dedicated AppRole.
- Harbor is intentionally **not** an Argo application. It moved to Discovery
  on 2026-06-22 to avoid the registry self-hosting bootstrap trap.
- The in-cluster Vault application was removed on 2026-06-29. The remaining
  `platform/harbor/` and `platform/vault/` directories are historical scaffolds,
  not root-app children.

## Coupling

`homelab-iac` (tailnet ACL grants) · `servarr/machines/discovery` (SWAG household
ingress) · `desktop-nixos` (OpenBao host service, sops secret seed,
`_k3s-node.nix` registries.yaml, kubeconfig, bootstrap-secret reconciler). Land
the leaf here → grant ACL → seed secret → sync the reviewed revision as shown
above.

## Ops notes (grill follow-ups)

- **Bootstrap credentials are durable (P0 closed 2026-07-15).** `desktop-nixos`
  sops owns the dedicated read-only Argo deploy key and ESO AppRole secret ID.
  Kepler exposes only the Argo key and ESO AppRole material read-only to cp-1;
  a guest timer reconciles the repository Secret, the legacy AppRole, and the
  `platform`, `homelab`, and `home-services` AppRoles. Vault remains the
  runtime-secret SSOT.

- **Renovate is active** through the fleet shared preset. Chart and Actions
  updates land through PRs after `helm template | kubeconform` validation.
- **Branch protection / review gate:** every Argo application is manual while
  `main` is unprotected. Sync root and children at the reviewed commit as shown
  above. Restore auto-sync only after branch protection is enabled.
- **Cognee consumes the published `cognee-homelab` image.** Its
  [resources](apps/cognee/resources.yaml) pin the Harbor image by digest and
  declare the `cognee-harbor` pull secret. For a new deployment, verify the
  reviewed `homelab-iac` key, OpenBao handoff and registry credentials before
  syncing; check operation completion and health as described above.
- **Telemetry stays in cluster.** `alloy-metrics` writes to the monitoring
  namespace Prometheus receiver and cluster log collectors write to Loki's
  service address. Discovery remains only the household ingress and OpenBao
  dependency for these paths.
