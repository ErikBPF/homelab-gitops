# homelab-gitops

> **SSOT role (desktop-nixos RFC 2026-06-29):** owns **lab / prod-mimic k8s
> workloads** (Argo CD) on the kepler k3s cluster. Consumes the k3s substrate from
> desktop-nixos + secrets via ESO→OpenBao@Discovery.

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
clusters/kepler/apps/        one Argo Application per component (sync-wave ordered)
platform/<component>/        thin Helm wrapper chart: Chart.yaml (1 dep) + values.yaml
apps/<workload>/             actual workloads (later)
```

Wrapper-chart convention (Chart.yaml = one pinned dependency + values.yaml)
mirrors the `datahomelab-gitops` reference — Renovate-friendly.

## Bootstrap

```
just bootstrap     # helm install argocd + apply root app-of-apps
just status        # argocd app list
just ui            # how to reach the Argo UI
```

Sync the root first, then every child at the same reviewed merge commit:

```bash
SHA="$(git rev-parse HEAD)" # checked-out reviewed merge commit
argocd app sync root --revision "$SHA"
argocd app sync argocd traefik csi-driver-nfs external-secrets kube-state-metrics alloy-metrics keda jaeger otel-collector vcluster-pastelariadev trace-demo demo airflow --revision "$SHA"
```

For rollback, set `SHA` to the prior known-good commit and repeat both sync
commands.

Uses your existing kubeconfig (context `homelab`). The Argo UI is exposed
via the cluster ingress behind the kepler L4 LB (no LoadBalancer Service).

`vcluster-pastelariadev` provides the experimental `pastelariadev` context with
restricted Pod Security, quotas, network isolation, and a bootstrap Job that
pulls BusyBox through Harbor. Run `just connect-pastelariadev`; keep it running
and use `kubectl` from the connected shell.

## Implemented prerequisites

- **external-secrets** → **OpenBao on Discovery** (host service owned by
  `desktop-nixos`, Vault-compatible API, dedicated AppRole)
  + the `vault-approle` bootstrap Secret reconciled from desktop-nixos sops +
  an ACL grant `kepler -> discovery:8200` (homelab-iac). Wave -3.
- **csi-driver-nfs** (`nfs-fast` / `nfs-slow`) → kepler must NFS-export an SSD
  dataset and `/bulk` to the cluster subnet `10.250.0.0/24`. Implemented. Wave -2.
- **kube-state-metrics** → a scrape→remote_write path to discovery Prometheus
  through `alloy-metrics`. Implemented. Wave 0.

## Current applications

The root app manages 13 child applications (14 Argo Applications including
`root`):

- `argocd`, `traefik`, `csi-driver-nfs`, `external-secrets`,
  `kube-state-metrics`, `alloy-metrics`, `keda`, `jaeger`, `otel-collector`,
  `vcluster-pastelariadev`, `trace-demo`, `demo`, and `airflow`.
- Traefik is the default IngressClass behind Kepler's `.250` L4 endpoint.
- ESO reads runtime secrets from OpenBao@Discovery through a dedicated AppRole.
- Harbor is intentionally **not** an Argo application. It moved to Discovery
  on 2026-06-22 to avoid the registry self-hosting bootstrap trap.
- The in-cluster Vault application was removed on 2026-06-29. The remaining
  `platform/harbor/` and `platform/vault/` directories are historical scaffolds,
  not root-app children.

## Coupling

`homelab-iac` (tailnet ACL grants) · `servarr/machines/discovery` (Prometheus
remote_write consumer) · `desktop-nixos` (OpenBao host service, sops secret seed, `_k3s-node.nix`
registries.yaml, kubeconfig, bootstrap-secret reconciler). Land the leaf here →
grant ACL → seed secret → sync the reviewed revision as shown above.

## Ops notes (grill follow-ups)

- **Bootstrap credentials are durable (P0 closed 2026-07-15).** `desktop-nixos`
  sops owns the dedicated read-only Argo deploy key and ESO AppRole secret ID.
  Kepler exposes only those two runtime files read-only to cp-1; a guest timer
  reconciles `argocd/homelab-gitops-repo` and
  `external-secrets/vault-approle`. `just test-k3s-bootstrap-recovery` in
  `desktop-nixos` deletes both exact Secrets and verifies their recreation plus
  `ClusterSecretStore Ready=True`. Vault remains runtime-secret SSOT.

- **Renovate is active** through the fleet shared preset. Chart and Actions
  updates land through PRs after `helm template | kubeconform` validation.
- **Branch protection / review gate:** every Argo application is manual while
  `main` is unprotected. Sync root and children at the reviewed commit as shown
  above. Restore auto-sync only after branch protection is enabled.
- **alloy-metrics → discovery Prometheus** and the in-cluster log/secret paths
  use discovery's **raw tailnet IP** (`100.76.140.121`) because pods can't resolve
  MagicDNS. Stable, but if discovery's TS IP ever changes, update it in
  `platform/alloy-metrics/values.yaml` (and the Nix cluster Alloy / ESO CSS).
