# platform-gitops

Production-mimicking platform layer for the **kepler** k3s cluster
(`pastelariadev`), deployed via **Argo CD** (GitOps). A sister repo of
`desktop-nixos`, on the **servarr strategy**: a git repo + `just` recipes,
applied to the cluster — **not** baked into the NixOS flake.

See the RFC: `desktop-nixos/docs/proposals/2026-06-20-cluster-platform-gitops.md`.

## Model

- **The NixOS flake owns the substrate** (k3s, networking/NAT, the L4 LB, PSA
  admission, etcd snapshots, and the existing `autoDeployCharts` ingress-nginx +
  Alloy). Those stay in `desktop-nixos`. **This repo owns the platform/workload
  layer on top.** Disjoint objects — no controller fights.
- **Argo CD is bootstrapped once** by `just bootstrap` (helm install +
  `kubectl apply` the root app-of-apps), then pulls everything from this repo,
  including its own chart ("Argo manages Argo"). Daily loop: edit → `git push` →
  Argo self-heals (or `just sync`).

## Layout

```
bootstrap/root-app.yaml      app-of-apps; kubectl apply'd by `just bootstrap`
argocd/                      Argo CD self-management (wrapper chart)
clusters/kepler/apps/        one Argo Application per component (sync-wave ordered)
platform/<component>/        thin Helm wrapper chart: Chart.yaml (1 dep) + values.yaml
apps/<workload>/             actual workloads (later)
```

Wrapper-chart convention (Chart.yaml = one pinned dependency + values.yaml)
mirrors the `dataplatform-gitops` reference — Renovate-friendly.

## Bootstrap

```
just bootstrap     # helm install argocd + apply root app-of-apps
just status        # argocd app list
just sync          # argocd app sync root (force a pull)
just ui            # how to reach the Argo UI
```

Uses your existing kubeconfig (context `pastelariadev`). The Argo UI is exposed
via the cluster ingress behind the kepler L4 LB (no LoadBalancer Service).

## Prerequisites per component (open — see RFC `TODO(erik)`)

- **external-secrets** → a **Vault on discovery** (servarr docker stack, AppRole)
  + the `vault-approle` secret seeded via sops-nix on kepler + an ACL grant
  `kepler -> discovery:8200` (homelab-iac). Wave -3.
- **csi-driver-nfs** (`nfs-fast` / `nfs-slow`) → kepler must NFS-export an SSD
  dataset and `/bulk` to the cluster subnet `10.250.0.0/24`. Wave -2.
- **kube-state-metrics** → a scrape→remote_write path to discovery Prometheus
  (extend the cluster Alloy with a metrics pipeline, or a dedicated Alloy-metrics
  here). Wave 0.

## Components scaffolded

argocd, traefik (replaces ingress-nginx), csi-driver-nfs (nfs-fast/nfs-slow),
external-secrets (→ Vault@discovery), kube-state-metrics, harbor (registry +
proxy-cache), keda, jaeger (all-in-one), otel-collector (OTLP → Jaeger). Chart
versions verified against the live repos + `helm template`-validated.

**Not deployable yet:** Argo pulls from a git REMOTE — push this repo and set the
real `repoURL` in `bootstrap/root-app.yaml` + `clusters/kepler/apps/*` first.
external-secrets also needs Vault@discovery; external-dns is still deferred
(AdGuard lives on discovery).

## Coupling

`homelab-iac` (tailnet ACL grants) · `servarr/machines/discovery` (Vault stack,
Prometheus remote_write) · `desktop-nixos` (sops secret seed, `_k3s-node.nix`
registries.yaml, kubeconfig). Land the leaf here → grant ACL → seed secret →
`just sync`.
