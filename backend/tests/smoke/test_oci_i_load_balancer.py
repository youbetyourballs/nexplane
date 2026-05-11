"""
Smoke Test Phase OCI_I — Load Balancer
Requires: OCI credentials, a live subnet OCID in the test compartment.
WARNING: OCI LB creation takes up to 15 minutes and may incur costs.
"""
import pytest
import os
from tests.smoke.helpers import create_and_execute_cr, rollback_cr, get_asset_by_metadata_field

SUBNET_IDS = os.getenv("OCI_SMOKE_SUBNET_IDS", "").split(",")
COMPARTMENT_ID = os.getenv("OCI_SMOKE_COMPARTMENT_ID", "")
OCI_CONNECTOR_ID = os.getenv("OCI_SMOKE_CONNECTOR_ID", "")


@pytest.mark.skipif(not SUBNET_IDS or not SUBNET_IDS[0], reason="OCI_SMOKE_SUBNET_IDS not set")
@pytest.mark.asyncio
@pytest.mark.timeout(1200)  # 20 min timeout for slow LB provisioning
async def test_oci_i_load_balancer_lifecycle(api_client, org_id):
    """Create LB → backend set → listener → delete LB (via rollback)."""
    # Step 1: Create Load Balancer
    lb_cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_I Create LB",
        "change_type": "oci_load_balancer_create",
        "target_asset_ids": [],
        "desired_outcome": {
            "compartment_id": COMPARTMENT_ID,
            "display_name": "nexplane-smoke-lb",
            "shape_name": "flexible",
            "shape_min_mbps": 10,
            "shape_max_mbps": 100,
            "subnet_ids": [s for s in SUBNET_IDS if s],
            "is_private": True,
        },
    }, connector_id=OCI_CONNECTOR_ID)

    # Verify load_balancer asset in inventory
    asset = await get_asset_by_metadata_field(api_client, org_id, asset_type="load_balancer", name="nexplane-smoke-lb")
    lb_id = asset["asset_metadata"]["load_balancer_id"] if asset else None
    assert lb_id, "load_balancer asset not found in inventory after creation"

    if lb_id:
        # Step 2: Create backend set
        await create_and_execute_cr(api_client, org_id, {
            "title": "OCI_I Create Backend Set",
            "change_type": "oci_backend_set_create",
            "target_asset_ids": [],
            "desired_outcome": {
                "load_balancer_id": lb_id,
                "name": "nexplane-smoke-bs",
                "policy": "ROUND_ROBIN",
            },
        }, connector_id=OCI_CONNECTOR_ID)

        # Step 3: Create listener
        await create_and_execute_cr(api_client, org_id, {
            "title": "OCI_I Create Listener",
            "change_type": "oci_listener_create",
            "target_asset_ids": [],
            "desired_outcome": {
                "load_balancer_id": lb_id,
                "name": "nexplane-smoke-listener",
                "default_backend_set": "nexplane-smoke-bs",
                "port": 80,
                "protocol": "HTTP",
            },
        }, connector_id=OCI_CONNECTOR_ID)

    # Primary cleanup: rollback the LB creation (triggers delete_load_balancer)
    await rollback_cr(api_client, lb_cr_id)
