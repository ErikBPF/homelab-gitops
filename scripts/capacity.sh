#!/usr/bin/env bash
set -euo pipefail

context=${KUBE_CONTEXT:-homelab}
kepler=${KEPLER_HOST:-192.168.10.230}

kubectl config get-contexts "$context" >/dev/null
kubectl --context "$context" get nodes -o wide
kubectl --context "$context" top nodes
kubectl --context "$context" describe nodes
kubectl --context "$context" get pods -A \
  -o custom-columns='NAMESPACE:.metadata.namespace,NAME:.metadata.name,NODE:.spec.nodeName,CPU_REQUESTS:.spec.containers[*].resources.requests.cpu,MEMORY_REQUESTS:.spec.containers[*].resources.requests.memory'
kubectl --context "$context" get storageclass,persistentvolume,persistentvolumeclaim -A
ssh -p 2222 "erik@$kepler" 'free -b; nproc; uptime'
