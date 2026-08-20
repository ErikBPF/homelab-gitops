# homelab-gitops — bootstrap + ops recipes (servarr-style)
# Uses your existing kubeconfig context for the stable homelab cluster.

set shell := ["bash", "-uc"]

ctx := "homelab"
ns  := "argocd"

# One-time: install Argo CD (local wrapper chart), then apply the root app-of-apps.
# Argo then reconciles everything in this repo — including its own release.
bootstrap:
    helm repo add argo https://argoproj.github.io/argo-helm >/dev/null 2>&1 || true
    helm repo update argo
    helm dependency update ./argocd
    helm upgrade --install argocd ./argocd \
        --namespace {{ns}} --create-namespace --kube-context {{ctx}} --wait
    kubectl --context {{ctx}} apply -f bootstrap/root-app.yaml
    @echo ":: bootstrapped. 'just password' for the admin pw, 'just ui' for access."

# Sync one app at an explicitly reviewed Git revision.
sync revision app="root":
    kubectl --context {{ctx}} -n {{ns}} exec statefulset/argocd-application-controller -- argocd app sync {{app}} --core --revision {{revision}}

# What Argo sees.
status:
    kubectl --context {{ctx}} -n {{ns}} get applications -o wide

# Open a shell connected to the development vCluster; exiting stops its proxy.
connect-pastelariadev:
    nix run nixpkgs#vcluster -- connect pastelariadev \
        --namespace vcluster-pastelariadev \
        --context {{ctx}} \
        -- bash

# Read-only control-plane, workload, event, and Harbor pull proof for agents.
diagnose-pastelariadev:
    kubectl --context {{ctx}} -n vcluster-pastelariadev get statefulset,pods,pvc
    nix run nixpkgs#vcluster -- connect pastelariadev \
        --namespace vcluster-pastelariadev \
        --context {{ctx}} \
        --background-proxy=false \
        -- bash -ceu 'kubectl get nodes,pods -A; kubectl get events -A --sort-by=.lastTimestamp | tail -n 30; kubectl wait --for=condition=complete job/harbor-pull-smoke-alpine-3-22 --timeout=2m; test "$(kubectl logs job/harbor-pull-smoke-alpine-3-22)" = harbor-pull-ok'

# One-time migration: copy current Helm runtime values into Vault without output.
# Reads an OpenBao operator token from stdin.
capture-airflow-runtime-to-vault:
    scripts/capture-airflow-runtime-to-vault.sh

status-alloy:
    kubectl --context {{ctx}} -n monitoring get pods -l app.kubernetes.io/instance=alloy-metrics -o wide
    kubectl --context {{ctx}} -n monitoring describe pods -l app.kubernetes.io/instance=alloy-metrics
    kubectl --context {{ctx}} -n monitoring logs -l app.kubernetes.io/instance=alloy-metrics -c alloy --tail=100

restart-alloy:
    kubectl --context {{ctx}} -n monitoring rollout restart deployment/alloy-metrics
    kubectl --context {{ctx}} -n monitoring rollout status deployment/alloy-metrics --timeout=2m

diff app="root":
    kubectl --context {{ctx}} -n {{ns}} exec statefulset/argocd-application-controller -- argocd app diff {{app}} --core

# Validate charts render before pushing (catches bad values locally).
template component:
    helm dependency build ./platform/{{component}}
    helm template {{component}} ./platform/{{component}}

ui:
    @echo "Argo UI (via ingress): https://argocd.k8s.pastelariadev.com"
    @echo "or: kubectl --context {{ctx}} -n {{ns}} port-forward svc/argocd-server 8080:443"

password:
    kubectl --context {{ctx}} -n {{ns}} get secret argocd-initial-admin-secret \
        -o jsonpath='{.data.password}' | base64 -d; echo

# Stamp the real git remote into every Argo Application + the root app (run once
# after creating + pushing the repo, if the URL differs from the placeholder).
set-repo url:
    grep -rl 'github.com/ErikBPF/homelab-gitops' bootstrap clusters \
        | xargs sed -i "s|ssh://git@ssh.github.com:443/ErikBPF/homelab-gitops.git|{{url}}|g"
    @echo ":: repoURL set to {{url}}"
