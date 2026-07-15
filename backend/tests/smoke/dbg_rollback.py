import sys, json, time
sys.path.insert(0, '/app/tests/smoke')
from smoke_helpers import NexplaneClient, _get_oci_blockstorage_client, _get_oci_creds

client = NexplaneClient('http://localhost:8000', 'admin@acme.example', 'admin123')
base = client.base
creds = _get_oci_creds()
compartment = creds.get('compartment_id', creds.get('tenancy', ''))
bs = _get_oci_blockstorage_client()
vols = bs.list_volumes(compartment_id=compartment).data
volume_id = next(v.id for v in vols if v.lifecycle_state == 'AVAILABLE')

resp = client.client.post(f'{base}/change-requests', json={
    'title': 'dbg tag', 'change_type': 'catalog_action',
    'desired_outcome': {
        'connector_type': 'oci', 'action_id': 'oci_tag_block_volume',
        'params': {'volume_id': volume_id, 'freeform_tags': {'dbg': 't1'}},
    },
})
cr_id = resp.json()['id']
for path in ['plan', 'submit-for-approval']:
    client.client.post(f'{base}/change-requests/{cr_id}/{path}')
client.client.post(f'{base}/change-requests/{cr_id}/approve',
                   json={'decision': 'approved', 'comment': 'dbg'})
client.client.post(f'{base}/change-requests/{cr_id}/execute')
for _ in range(20):
    cr = client.client.get(f'{base}/change-requests/{cr_id}').json()
    if cr['status'] in ('completed', 'failed'):
        break
    time.sleep(3)
print('Execute status:', cr['status'])

client.client.post(f'{base}/change-requests/{cr_id}/rollback')
for _ in range(20):
    cr = client.client.get(f'{base}/change-requests/{cr_id}').json()
    if cr['status'] in ('rolled_back', 'rollback_failed'):
        break
    time.sleep(3)
print('Rollback status:', cr['status'])
for run in cr.get('execution_runs', []):
    print('--- workflow_id:', run.get('workflow_id', 'none'))
    print('    result:', json.dumps(run.get('result', {}), default=str)[:800])
