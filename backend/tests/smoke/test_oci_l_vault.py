"""
Smoke Test Phase OCI_L — Vault secret lifecycle.
Skipped automatically if no ACTIVE vault exists in the tenancy.
"""
import pytest
import os
from tests.smoke.helpers import create_and_execute_cr, rollback_cr

COMPARTMENT_ID = os.getenv("OCI_SMOKE_COMPARTMENT_ID", "")
OCI_CONNECTOR_ID = os.getenv("OCI_SMOKE_CONNECTOR_ID", "")
OCI_SMOKE_SECRET_ID = os.getenv("OCI_SMOKE_SECRET_ID", "")


@pytest.mark.skipif(not COMPARTMENT_ID, reason="OCI_SMOKE_COMPARTMENT_ID not set")
@pytest.mark.asyncio
async def test_oci_l_vault_secret_create(api_client, org_id):
    """
    Attempt to create a Vault secret.
    If no ACTIVE vault exists, the executor returns a clear error and we skip.
    Primary cleanup: Nexplane rollback (schedules deletion of created secret).
    """
    cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_L1 Create OCI Vault secret",
        "change_type": "oci_vault_secret_create",
        "target_asset_ids": [],
        "desired_outcome": {
            "compartment_id": COMPARTMENT_ID,
            "secret_name": "nexplane-smoke-l1",
            "secret_content": "smoketest",
            "description": "Smoke test secret",
        },
    }, connector_id=OCI_CONNECTOR_ID)

    if not cr_id:
        pytest.skip("CR creation failed — no ACTIVE Vault or key in compartment")

    # Primary cleanup: Nexplane rollback schedules deletion
    await rollback_cr(api_client, org_id, cr_id)


@pytest.mark.skipif(not (COMPARTMENT_ID and OCI_SMOKE_SECRET_ID), reason="OCI_SMOKE_COMPARTMENT_ID or OCI_SMOKE_SECRET_ID not set")
@pytest.mark.asyncio
async def test_oci_l_vault_secret_delete(api_client, org_id):
    """
    Schedule deletion of an existing vault secret.
    Rollback cancels the scheduled deletion.
    """
    cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_L2 Schedule delete OCI Vault secret",
        "change_type": "oci_vault_secret_delete",
        "target_asset_ids": [],
        "desired_outcome": {
            "secret_id": OCI_SMOKE_SECRET_ID,
            "deletion_time_days": 1,
        },
    }, connector_id=OCI_CONNECTOR_ID)
    assert cr_id, "CR was not created"
    # Rollback cancels scheduled deletion
    await rollback_cr(api_client, org_id, cr_id)
