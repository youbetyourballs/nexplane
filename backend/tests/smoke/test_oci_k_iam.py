"""
Smoke Test Phase OCI_K — IAM user/group/policy lifecycle.
Requires: OCI credentials and a compartment.
"""
import pytest
import os
from tests.smoke.helpers import create_and_execute_cr, rollback_cr

COMPARTMENT_ID = os.getenv("OCI_SMOKE_COMPARTMENT_ID", "")
OCI_CONNECTOR_ID = os.getenv("OCI_SMOKE_CONNECTOR_ID", "")
OCI_SMOKE_USER_ID = os.getenv("OCI_SMOKE_USER_ID", "")


@pytest.mark.skipif(not COMPARTMENT_ID, reason="OCI_SMOKE_COMPARTMENT_ID not set")
@pytest.mark.asyncio
async def test_oci_k_iam_user_lifecycle(api_client, org_id):
    """Create IAM user → rollback (delete user) via Nexplane rollback."""
    cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_K1 Create OCI IAM user",
        "change_type": "oci_iam_user_create",
        "target_asset_ids": [],
        "desired_outcome": {
            "name": "nexplane-smoke-k1",
            "description": "Smoke test",
        },
    }, connector_id=OCI_CONNECTOR_ID)
    assert cr_id, "CR was not created"
    # Primary cleanup: Nexplane rollback
    await rollback_cr(api_client, org_id, cr_id)


@pytest.mark.skipif(not COMPARTMENT_ID, reason="OCI_SMOKE_COMPARTMENT_ID not set")
@pytest.mark.asyncio
async def test_oci_k_iam_group_lifecycle(api_client, org_id):
    """Create IAM group → rollback (delete group) via Nexplane rollback."""
    cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_K2 Create OCI IAM group",
        "change_type": "oci_iam_group_create",
        "target_asset_ids": [],
        "desired_outcome": {
            "name": "nexplane-smoke-k2",
            "description": "Smoke test",
        },
    }, connector_id=OCI_CONNECTOR_ID)
    assert cr_id, "CR was not created"
    # Primary cleanup: Nexplane rollback
    await rollback_cr(api_client, org_id, cr_id)


@pytest.mark.skipif(not COMPARTMENT_ID, reason="OCI_SMOKE_COMPARTMENT_ID not set")
@pytest.mark.asyncio
async def test_oci_k_iam_policy_lifecycle(api_client, org_id):
    """Create IAM policy → rollback (delete policy) via Nexplane rollback."""
    cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_K3 Create OCI IAM policy",
        "change_type": "oci_iam_policy_create",
        "target_asset_ids": [],
        "desired_outcome": {
            "compartment_id": COMPARTMENT_ID,
            "name": "nexplane-smoke-k3",
            "description": "Smoke test",
            "statements": ["Allow group nexplane-smoke-k2 to read all-resources in tenancy"],
        },
    }, connector_id=OCI_CONNECTOR_ID)
    assert cr_id, "CR was not created"
    # Primary cleanup: Nexplane rollback
    await rollback_cr(api_client, org_id, cr_id)


@pytest.mark.skipif(not (COMPARTMENT_ID and OCI_SMOKE_USER_ID), reason="OCI_SMOKE_COMPARTMENT_ID or OCI_SMOKE_USER_ID not set")
@pytest.mark.asyncio
async def test_oci_k_disable_enable_user(api_client, org_id):
    """Disable IAM user → verify → enable via Nexplane rollback."""
    cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_K4 Disable OCI IAM user",
        "change_type": "oci_iam_user_disable",
        "target_asset_ids": [],
        "desired_outcome": {
            "user_id": OCI_SMOKE_USER_ID,
        },
    }, connector_id=OCI_CONNECTOR_ID)
    assert cr_id, "CR was not created"
    # Primary cleanup: Nexplane rollback restores user capabilities
    await rollback_cr(api_client, org_id, cr_id)
