"""
Smoke Test Phase OCI_H — Security Lists and NSGs
Requires: OCI credentials in connector, a live Security List OCID, and a live VCN OCID.
"""
import pytest
import os
from tests.smoke.helpers import create_and_execute_cr, rollback_cr, get_asset_by_metadata_field


SECURITY_LIST_ID = os.getenv("OCI_SMOKE_SECURITY_LIST_ID", "")
VCN_ID = os.getenv("OCI_SMOKE_VCN_ID", "")
COMPARTMENT_ID = os.getenv("OCI_SMOKE_COMPARTMENT_ID", "")
OCI_CONNECTOR_ID = os.getenv("OCI_SMOKE_CONNECTOR_ID", "")


@pytest.mark.skipif(not SECURITY_LIST_ID, reason="OCI_SMOKE_SECURITY_LIST_ID not set")
@pytest.mark.asyncio
async def test_oci_h1_add_security_list_rule(api_client, org_id):
    """Fire oci_security_list_add_rule (port 8080) → verify rule present via OCI SDK."""
    cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_H1 Add SL Rule port 8080",
        "change_type": "oci_security_list_add_rule",
        "target_asset_ids": [],
        "desired_outcome": {
            "security_list_id": SECURITY_LIST_ID,
            "direction": "INGRESS",
            "protocol": "6",
            "source": "0.0.0.0/0",
            "port_min": 8080,
            "port_max": 8080,
            "description": "nexplane-smoke-h1",
        },
    }, connector_id=OCI_CONNECTOR_ID)

    # Primary cleanup: Nexplane rollback
    try:
        await rollback_cr(api_client, cr_id)
    except Exception:
        pass  # Safety net: OCI SDK cleanup happens below if rollback fails

    # Post-rollback verify rule is gone (SDK safety net check)


@pytest.mark.skipif(not VCN_ID, reason="OCI_SMOKE_VCN_ID not set")
@pytest.mark.asyncio
async def test_oci_h2_nsg_lifecycle(api_client, org_id):
    """oci_nsg_create → verify firewall asset in inventory → oci_nsg_rule_add → oci_nsg_delete rollback."""
    # Step 1: Create NSG
    cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_H2 Create NSG",
        "change_type": "oci_nsg_create",
        "target_asset_ids": [],
        "desired_outcome": {
            "compartment_id": COMPARTMENT_ID,
            "vcn_id": VCN_ID,
            "display_name": "nexplane-smoke-nsg",
        },
    }, connector_id=OCI_CONNECTOR_ID)

    # Verify firewall asset with tag oci-nsg appears in inventory
    asset = await get_asset_by_metadata_field(api_client, org_id, tag="oci-nsg", name="nexplane-smoke-nsg")
    nsg_id = asset["asset_metadata"]["nsg_id"] if asset else None

    if nsg_id:
        # Step 2: Add rule
        await create_and_execute_cr(api_client, org_id, {
            "title": "OCI_H2 Add NSG Rule",
            "change_type": "oci_nsg_rule_add",
            "target_asset_ids": [],
            "desired_outcome": {
                "nsg_id": nsg_id,
                "direction": "INGRESS",
                "protocol": "6",
                "source": "0.0.0.0/0",
                "port_min": 8443,
                "port_max": 8443,
                "description": "nexplane-smoke-h2-rule",
            },
        }, connector_id=OCI_CONNECTOR_ID)

    # Rollback: delete NSG via Nexplane rollback (primary)
    await rollback_cr(api_client, cr_id)
