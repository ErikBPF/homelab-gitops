#!/usr/bin/env python3
"""Offline recovery configuration contract; no cluster API."""
import json
from pathlib import Path
import subprocess
import yaml

ROOT = Path(__file__).resolve().parents[1]
render = list(yaml.safe_load_all(subprocess.check_output(['kubectl', 'kustomize', str(ROOT / 'platform/wazuh')], text=True)))
config = next(x for x in render if x['kind'] == 'ConfigMap' and x['metadata']['name'] == 'indexer-conf')
indexer = yaml.safe_load(config['data']['opensearch.yml'])
assert indexer.get('path.repo') == ['/var/lib/wazuh-indexer/recovery'], 'native snapshot repository must use existing indexer PVC'
policy = json.loads((ROOT / 'platform/wazuh/recovery/alerts-retention.json').read_text())
assert policy['policy']['default_state'] == 'retain'
assert policy['policy']['states'][0]['transitions'] == [{'state_name': 'delete', 'conditions': {'min_index_age': '30d'}}]
assert 'ism_template' not in policy['policy'], 'do not automatically attach deletion before restore proof'

# Recovery capture and backup authority stay gated until the Vault credential
# and dedicated OpenSearch snapshot role are provisioned and a restore is proven.
def absent(kind, name=None):
    assert not any(x['kind'] == kind and (name is None or x['metadata']['name'] == name) for x in render), \
        f'{kind} {name or ""} must not render before the recovery prerequisite is met'

absent('ExternalSecret', 'wazuh-snapshot-auth')
absent('CronJob')
absent('NetworkPolicy', 'wazuh-snapshot-only-egress')
absent('ServiceAccount', 'svc-desktop-nixos-wazuh-manager-scale')
for name in ['wazuh-manager-backup-scale', 'wazuh-manager-backup-metadata']:
    absent('Role', name)
absent('ClusterRole', 'wazuh-backup-bound-volume-metadata')

producer = list(yaml.safe_load_all((ROOT / 'platform/wazuh/recovery-producer.yaml').read_text()))
jobs = [x for x in producer if x['kind'] == 'CronJob']
assert len(jobs) == 1 and jobs[0]['spec']['suspend'] is True, 'recovery preparation must not run automatically'
pod = jobs[0]['spec']['jobTemplate']['spec']['template']['spec']
assert pod['automountServiceAccountToken'] is False
assert jobs[0]['spec']['concurrencyPolicy'] == 'Forbid'
assert jobs[0]['spec']['jobTemplate']['spec']['activeDeadlineSeconds'] == 600
ca = next(x for x in pod['volumes'] if x['name'] == 'ca')['secret']
assert ca['items'] == [{'key': 'root-ca.pem', 'path': 'root-ca.pem'}]
snapshot_job = jobs[0]
mounts = snapshot_job['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]['volumeMounts']
assert {x.get('subPath') for x in mounts if x['name'] == 'indexer'} == {'recovery', 'recovery-captures'}
assert not any(x['mountPath'] == '/var/lib/wazuh-indexer' for x in mounts)
job_pod = snapshot_job['spec']['jobTemplate']['spec']['template']['spec']
assert 'fsGroup' not in job_pod['securityContext']
proof = next(x for x in job_pod['volumes'] if x['name'] == 'proof')
assert proof['nfs'] == {'server': '10.250.0.1', 'path': '/bulk/k8s/wazuh-recovery-proof', 'readOnly': True}
assert next(x for x in mounts if x['name'] == 'proof')['readOnly'] is True
snapshot_policy = next(x for x in producer if x['kind'] == 'NetworkPolicy' and x['metadata']['name'] == 'wazuh-snapshot-only-egress')
assert {p['port'] for rule in snapshot_policy['spec']['egress'] for p in rule['ports']} == {53,9200}

rbac = list(yaml.safe_load_all((ROOT / 'platform/wazuh/recovery-rbac.yaml').read_text()))
roles = [x for x in rbac if x['kind'] == 'Role' and x['metadata']['name'] == 'wazuh-manager-backup-scale']
assert len(roles) == 1, 'missing scoped backup scale authority'
role = roles[0]
assert role['rules'] == [{'apiGroups': ['apps'], 'resources': ['statefulsets/scale'], 'resourceNames': ['wazuh-manager-master', 'wazuh-manager-worker'], 'verbs': ['get', 'patch']}]
metadata_role = next(x for x in rbac if x['kind'] == 'Role' and x['metadata']['name'] == 'wazuh-manager-backup-metadata')
assert metadata_role['rules'] == [
    {'apiGroups': [''], 'resources': ['pods'], 'resourceNames': ['wazuh-manager-master-0', 'wazuh-manager-worker-0'], 'verbs': ['get']},
    {'apiGroups': [''], 'resources': ['persistentvolumeclaims'], 'resourceNames': ['wazuh-manager-master-wazuh-manager-master-0', 'wazuh-manager-worker-wazuh-manager-worker-0'], 'verbs': ['get']},
]
pv_role = next(x for x in rbac if x['kind'] == 'ClusterRole' and x['metadata']['name'] == 'wazuh-backup-bound-volume-metadata')
assert pv_role['rules'] == [{'apiGroups': [''], 'resources': ['persistentvolumes'], 'resourceNames': ['pvc-4c197eed-4b28-431b-bb4c-99e17fc3d460', 'pvc-24bb0968-7b98-463a-af73-249a186fddc8', 'pvc-f310f72b-ce47-44df-929f-1fdf16c145be'], 'verbs': ['get']}]
assert not any(x['kind'] == 'Secret' for x in render), 'never materialize a standing token in source'

print('WAZUH_RECOVERY_CONFIG_OK')
app = yaml.safe_load((ROOT / 'clusters/kepler/apps/wazuh.yaml').read_text())
scoped = [x for x in app['spec']['ignoreDifferences'] if x.get('name')]
assert scoped == [{'group': 'apps', 'kind': 'StatefulSet', 'name': name, 'namespace': 'wazuh', 'jsonPointers': ['/spec/replicas']} for name in ['wazuh-manager-master', 'wazuh-manager-worker']]
assert 'RespectIgnoreDifferences=true' in app['spec']['syncPolicy']['syncOptions']
root_render = list(yaml.safe_load_all(subprocess.check_output(['kubectl', 'kustomize', str(ROOT / 'clusters/kepler/apps')], text=True)))
observer = next(x for x in root_render if x['kind'] == 'Role' and x['metadata']['name'] == 'wazuh-backup-argo-observer')
assert observer['metadata']['namespace'] == 'argocd'
assert observer['rules'] == [{'apiGroups': ['argoproj.io'], 'resources': ['applications'], 'resourceNames': ['wazuh'], 'verbs': ['get']}]
binding = next(x for x in root_render if x['kind'] == 'RoleBinding' and x['metadata']['name'] == 'wazuh-backup-argo-observer')
assert binding['metadata']['namespace'] == 'argocd'
assert binding['subjects'] == [{'kind': 'ServiceAccount', 'name': 'svc-desktop-nixos-wazuh-manager-scale', 'namespace': 'wazuh'}]
for name in ['wazuh-manager-master', 'wazuh-manager-worker']:
    pod = next(x for x in render if x['kind'] == 'StatefulSet' and x['metadata']['name'] == name)['spec']['template']['spec']
    container = next(x for x in pod['containers'] if x['name'] == 'wazuh-manager')
    assert 'preStop' in container.get('lifecycle', {}), 'manager termination lacks graceful database stop proof'
    assert container['lifecycle']['preStop']['exec']['command'] == ['/var/ossec/framework/python/bin/python3', '/recovery/manager-prestop.py']
    assert pod['terminationGracePeriodSeconds'] == 90
    assert {'name': 'POD_UID', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.uid'}}} in container['env']

indexer_sts = next(x for x in render if x['kind'] == 'StatefulSet' and x['metadata']['name'] == 'wazuh-indexer')
initializer = next(x for x in indexer_sts['spec']['template']['spec']['initContainers'] if x['name'] == 'volume-mount-hack')
assert 'chown -R 1000:1000 /var/lib/wazuh-indexer\n' not in initializer['command'][-1]
assert indexer_sts['spec']['template']['spec']['securityContext']['fsGroupChangePolicy'] == 'OnRootMismatch'
shared_policy = next(x for x in render if x['kind'] == 'NetworkPolicy' and x['metadata']['name'] == 'allow-runtime')
assert shared_policy['spec']['podSelector']['matchExpressions'] == [{'key':'app','operator':'NotIn','values':['wazuh-index-snapshot']}]
