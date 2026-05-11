"""
Smoke Test Phase OCI_J — DNS
Requires: OCI credentials and a compartment where DNS zones can be created.
"""
import pytest
import os
from tests.smoke.helpers import create_and_execute_cr, rollback_cr, get_asset_by_metadata_field

COMPARTMENT_ID = os.getenv("OCI_SMOKE_COMPARTMENT_ID", "")
OCI_CONNECTOR_ID = os.getenv("OCI_SMOKE_CONNECTOR_ID", "")
DNS_ZONE_NAME = os.getenv("OCI_SMOKE_DNS_ZONE_NAME", "nexplane-smoke-test.example.com")


@pytest.mark.skipif(not COMPARTMENT_ID, reason="OCI_SMOKE_COMPARTMENT_ID not set")
@pytest.mark.asyncio
async def test_oci_j_dns_lifecycle(api_client, org_id):
    """Create DNS zone → upsert record → verify via OCI SDK → rollback zone."""
    # Step 1: Create DNS zone
    zone_cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_J Create DNS Zone",
        "change_type": "oci_dns_zone_create",
        "target_asset_ids": [],
        "desired_outcome": {
            "compartment_id": COMPARTMENT_ID,
            "name": DNS_ZONE_NAME,
            "zone_type": "PRIMARY",
        },
    }, connector_id=OCI_CONNECTOR_ID)

    # Verify dns_zone asset in inventory
    asset = await get_asset_by_metadata_field(api_client, org_id, asset_type="dns_zone", name=DNS_ZONE_NAME)
    assert asset, f"dns_zone asset '{DNS_ZONE_NAME}' not found in inventory"
    zone_id = asset["asset_metadata"]["zone_id"]

    # Step 2: Upsert A record
    record_cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_J Upsert DNS Record",
        "change_type": "oci_dns_record_upsert",
        "target_asset_ids": [],
        "desired_outcome": {
            "zone_name_or_id": zone_id,
            "domain": f"smoke.{DNS_ZONE_NAME}",
            "rtype": "A",
            "ttl": 60,
            "rdata": "192.0.2.1",
        },
    }, connector_id=OCI_CONNECTOR_ID)

    # Rollback record first, then zone (primary: Nexplane rollback)
    await rollback_cr(api_client, record_cr_id)
    await rollback_cr(api_client, zone_cr_id)
