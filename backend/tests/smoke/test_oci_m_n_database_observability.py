"""
Smoke Tests Phase OCI_M — Autonomous Database lifecycle
Smoke Tests Phase OCI_N — Monitoring alarm and Logging

Requires OCI credentials set via environment variables and the OCI connector configured.
ADB provisioning is slow — allow up to 20 min per step.
MySQL tests are skipped by default (slow provisioning, costly on paid tiers).
"""
import pytest
import os
from tests.smoke.helpers import create_and_execute_cr, rollback_cr

COMPARTMENT_ID = os.getenv("OCI_SMOKE_COMPARTMENT_ID", "")
OCI_CONNECTOR_ID = os.getenv("OCI_SMOKE_CONNECTOR_ID", "")


def _get_oci_config_from_env() -> dict:
    """Build OCI SDK config dict from environment variables."""
    return {
        "user": os.getenv("OCI_USER", ""),
        "key_content": os.getenv("OCI_PRIVATE_KEY", ""),
        "fingerprint": os.getenv("OCI_FINGERPRINT", ""),
        "tenancy": os.getenv("OCI_TENANCY", ""),
        "region": os.getenv("OCI_REGION", "us-ashburn-1"),
    }


# ---------------------------------------------------------------------------
# Phase OCI_M — Autonomous Database lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not COMPARTMENT_ID, reason="OCI_SMOKE_COMPARTMENT_ID not set")
@pytest.mark.asyncio
@pytest.mark.slow
async def test_oci_m_adb_create(api_client, org_id):
    """Create an OCI Autonomous Database and verify it is discoverable."""
    cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_M1 Create Autonomous Database",
        "change_type": "oci_adb_create",
        "target_asset_ids": [],
        "desired_outcome": {
            "compartment_id": COMPARTMENT_ID,
            "display_name": "nexplane-smoke-adb",
            "db_name": "smokeadb",
            "admin_password": "Nexplane1234!",
            "db_workload": "OLTP",
            "cpu_core_count": 1,
            "data_storage_size_in_tbs": 1,
            "is_free_tier": True,
            "license_model": "LICENSE_INCLUDED",
            "rollback_strategy": "oci_adb_delete",
        },
    }, connector_id=OCI_CONNECTOR_ID)

    if not cr_id:
        pytest.skip("CR creation failed — check OCI credentials and compartment ID")

    # Primary cleanup: Nexplane rollback (deletes ADB)
    await rollback_cr(api_client, org_id, cr_id)


@pytest.mark.skipif(not COMPARTMENT_ID, reason="OCI_SMOKE_COMPARTMENT_ID not set")
@pytest.mark.asyncio
@pytest.mark.slow
async def test_oci_m_adb_stop_start(api_client, org_id):
    """Stop then start an ADB. Requires ADB OCID in OCI_SMOKE_ADB_ID env var."""
    adb_id = os.getenv("OCI_SMOKE_ADB_ID", "")
    if not adb_id:
        pytest.skip("OCI_SMOKE_ADB_ID not set — skipping stop/start test")

    # Stop
    stop_cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_M2 Stop ADB",
        "change_type": "oci_adb_stop",
        "target_asset_ids": [],
        "desired_outcome": {
            "db_id": adb_id,
            "rollback_strategy": "oci_adb_start",
        },
    }, connector_id=OCI_CONNECTOR_ID)

    if not stop_cr_id:
        pytest.skip("Stop CR creation failed")

    # Start via rollback
    await rollback_cr(api_client, org_id, stop_cr_id)


@pytest.mark.skipif(not COMPARTMENT_ID, reason="OCI_SMOKE_COMPARTMENT_ID not set")
@pytest.mark.asyncio
@pytest.mark.slow
async def test_oci_m_adb_backup(api_client, org_id):
    """Create a manual ADB backup. Requires ADB OCID in OCI_SMOKE_ADB_ID env var."""
    adb_id = os.getenv("OCI_SMOKE_ADB_ID", "")
    if not adb_id:
        pytest.skip("OCI_SMOKE_ADB_ID not set — skipping backup test")

    cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_M3 Backup ADB",
        "change_type": "oci_adb_backup",
        "target_asset_ids": [],
        "desired_outcome": {
            "db_id": adb_id,
            "display_name": "nexplane-smoke-backup",
        },
    }, connector_id=OCI_CONNECTOR_ID)

    if not cr_id:
        pytest.skip("Backup CR creation failed")

    # Rollback: delete the backup
    await rollback_cr(api_client, org_id, cr_id)


# ---------------------------------------------------------------------------
# Phase OCI_N — Monitoring alarm + Logging
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not COMPARTMENT_ID, reason="OCI_SMOKE_COMPARTMENT_ID not set")
@pytest.mark.asyncio
async def test_oci_n_alarm_create_delete(api_client, org_id):
    """Create an OCI Monitoring alarm then delete it via rollback."""
    cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_N1 Create OCI Monitoring Alarm",
        "change_type": "oci_alarm_create",
        "target_asset_ids": [],
        "desired_outcome": {
            "compartment_id": COMPARTMENT_ID,
            "display_name": "nexplane-smoke-alarm",
            "namespace": "oci_computeagent",
            "query": "CpuUtilization[1m].mean() > 80",
            "severity": "CRITICAL",
            "body": "Smoke test alarm",
            "destinations": [],
            "is_enabled": True,
            "rollback_strategy": "oci_alarm_delete",
        },
    }, connector_id=OCI_CONNECTOR_ID)

    if not cr_id:
        pytest.skip("CR creation failed — check OCI credentials and compartment ID")

    # Primary cleanup: Nexplane rollback deletes the alarm
    await rollback_cr(api_client, org_id, cr_id)


@pytest.mark.skipif(not COMPARTMENT_ID, reason="OCI_SMOKE_COMPARTMENT_ID not set")
@pytest.mark.asyncio
async def test_oci_n_logging_enable(api_client, org_id):
    """Enable OCI Logging (create Log Group + Log) then rollback (delete both)."""
    cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_N2 Enable OCI Logging",
        "change_type": "oci_logging_enable",
        "target_asset_ids": [],
        "desired_outcome": {
            "compartment_id": COMPARTMENT_ID,
            "log_group_name": "nexplane-smoke-logs",
            "log_name": "nexplane-smoke-audit-log",
            "log_type": "AUDIT",
            "is_enabled": True,
            "retention_duration": 30,
            "rollback_strategy": "disable_logging",
        },
    }, connector_id=OCI_CONNECTOR_ID)

    if not cr_id:
        pytest.skip("CR creation failed — check OCI credentials and compartment ID")

    # Primary cleanup: Nexplane rollback deletes log group + log
    await rollback_cr(api_client, org_id, cr_id)


# ---------------------------------------------------------------------------
# MySQL tests — skipped by default (slow provisioning, costly on paid tiers)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not (COMPARTMENT_ID and os.getenv("OCI_SMOKE_INCLUDE_MYSQL")),
    reason="OCI_SMOKE_INCLUDE_MYSQL not set — skipping slow MySQL provisioning test",
)
@pytest.mark.asyncio
@pytest.mark.slow
async def test_oci_mysql_create_delete(api_client, org_id):
    """Create a MySQL HeatWave DB System then delete via rollback. Very slow (up to 25 min)."""
    subnet_id = os.getenv("OCI_SMOKE_SUBNET_ID", "")
    availability_domain = os.getenv("OCI_SMOKE_AVAILABILITY_DOMAIN", "")
    if not subnet_id or not availability_domain:
        pytest.skip("OCI_SMOKE_SUBNET_ID or OCI_SMOKE_AVAILABILITY_DOMAIN not set")

    cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_MYSQL Create MySQL HeatWave",
        "change_type": "oci_mysql_create",
        "target_asset_ids": [],
        "desired_outcome": {
            "compartment_id": COMPARTMENT_ID,
            "display_name": "nexplane-smoke-mysql",
            "admin_username": "nexplane",
            "admin_password": "Nexplane1234!",
            "shape_name": "MySQL.VM.Standard.E4.1.8GB",
            "mysql_version": "8.0.36",
            "subnet_id": subnet_id,
            "data_storage_size_in_gbs": 50,
            "availability_domain": availability_domain,
            "rollback_strategy": "oci_mysql_delete",
        },
    }, connector_id=OCI_CONNECTOR_ID)

    if not cr_id:
        pytest.skip("MySQL CR creation failed")

    # Primary cleanup: Nexplane rollback deletes the DB system
    await rollback_cr(api_client, org_id, cr_id)
