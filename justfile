# homelab-gitops — bootstrap + ops recipes (servarr-style)
# Uses your existing kubeconfig context for the stable homelab cluster.

set shell := ["bash", "-uc"]

ctx := "homelab"
ns  := "argocd"

test:
    bash scripts/validate.sh

capacity:
    bash scripts/capacity.sh

# One-time: install Argo CD (local wrapper chart), then apply the root app-of-apps.
# Argo then reconciles everything in this repo — including its own release.
bootstrap:
    helm repo add argo https://argoproj.github.io/argo-helm >/dev/null 2>&1 || true
    helm repo update argo
    helm dependency update ./argocd
    helm upgrade --install argocd ./argocd \
        --namespace {{ns}} --create-namespace --kube-context {{ctx}} --wait
    kubectl --context {{ctx}} apply -f clusters/kepler/apps/projects.yaml
    kubectl --context {{ctx}} apply -f bootstrap/root-app.yaml
    @echo ":: bootstrapped. 'just password' for the admin pw, 'just ui' for access."

# Sync one app at an explicitly reviewed Git revision.
sync revision app="root":
    scripts/sync.sh {{quote(revision)}} {{quote(app)}}

# One-time cutover: remove the retired Kepler work-test vcluster and its resources.
retire-vcluster-pastelariadev:
    scripts/retire-vcluster-pastelariadev.sh

# What Argo sees.
status:
    kubectl --context {{ctx}} -n {{ns}} get applications -o wide

# Value-free active Grafana inventory. Fails if Kubernetes or Grafana is unavailable.
grafana-alert-status:
    scripts/grafana-alert-status.sh

# One-time migration: copy current Helm runtime values into Vault without output.
# Reads an OpenBao operator token from stdin.
capture-airflow-runtime-to-vault:
    scripts/capture-airflow-runtime-to-vault.sh

# Recreate first-wave workload values and Wazuh certificates in Vault.
# Reads an OpenBao operator token from stdin; preserves only shared webhooks.
bootstrap-runtime-secrets:
    scripts/bootstrap-runtime-secrets.sh

# Rotate only Grafana's admin password; preserves all other monitoring values.
# Reads an OpenBao operator token from stdin and never prints secret material.
rotate-grafana-admin-password:
    scripts/rotate-grafana-admin-password.sh

# Install the cert-manager Cloudflare DNS-01 token without exposing its value.
# Reads the token from stdin.
bootstrap-monitoring-dns01-token:
    scripts/bootstrap-monitoring-dns01-token.sh

# Ask ESO to refresh every declared runtime secret immediately.
reconcile-runtime-secrets:
    kubectl --context {{ctx}} annotate externalsecrets.external-secrets.io --all --all-namespaces \
        force-sync="$(date +%s)" --overwrite

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
