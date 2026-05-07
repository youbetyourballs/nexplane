import sys, threading, asyncio
sys.path.insert(0, '/app/tests/smoke')
from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient

result = [None]
async def _get():
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector, ConnectorType
    from app.services.connector_service import _attach_credentials
    import sqlalchemy as sa
    async with AsyncSessionLocal() as db:
        res = await db.execute(sa.select(Connector).where(Connector.connector_type == ConnectorType.azure))
        conn = res.scalars().first()
        if conn:
            await _attach_credentials(conn, db)
            result[0] = getattr(conn, 'credentials', {})
def run():
    asyncio.run(_get())
t = threading.Thread(target=run); t.start(); t.join()

creds_dict = result[0] or {}
cred = ClientSecretCredential(tenant_id=creds_dict.get('tenant_id'), client_id=creds_dict.get('client_id'), client_secret=creds_dict.get('client_secret'))
compute = ComputeManagementClient(cred, creds_dict.get('subscription_id'))

filter_str = "location eq 'eastus2'"
skus = compute.resource_skus.list(filter=filter_str)
gen1_compatible = []
for s in skus:
    if s.resource_type != 'virtualMachines':
        continue
    caps = {c.name: c.value for c in (s.capabilities or [])}
    hyper_v = caps.get('HyperVGenerations', '')
    if 'V1' in hyper_v and not s.restrictions:
        gen1_compatible.append((s.name, hyper_v))
print('Gen1 no-restriction in eastus2:', gen1_compatible[:10])
