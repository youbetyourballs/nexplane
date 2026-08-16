# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Serverless container deploy live smoke tests.

Env vars:
  SMOKE_CLOUD_RUN_SERVICE    — Cloud Run service name
  SMOKE_CLOUD_RUN_REGION     — e.g. 'us-central1'
  SMOKE_CLOUD_RUN_IMAGE      — image to deploy (must differ from current)
  SMOKE_AZURE_CA_RG          — Container Apps resource group
  SMOKE_AZURE_CA_APP         — Container App name
  SMOKE_AZURE_CA_IMAGE       — image to deploy
  SMOKE_OCI_CI_COMPARTMENT   — OCI compartment OCID
  SMOKE_OCI_CI_INSTANCE_ID   — existing container instance OCID
  SMOKE_OCI_CI_IMAGE         — image to deploy
"""
import os
import pytest
from app.connectors.executors.gcp.cloud_run_deploy import execute as cr_execute, rollback as cr_rollback
from app.connectors.executors.azure.azure_container_apps_deploy import execute as ca_execute, rollback as ca_rollback
from app.connectors.executors.oci.oci_container_instances_deploy import execute as oci_execute, rollback as oci_rollback


@pytest.mark.smoke
@pytest.mark.skipif(not os.getenv("SMOKE_CLOUD_RUN_SERVICE"), reason="SMOKE_CLOUD_RUN_SERVICE not set")
async def test_cloud_run_deploy_and_rollback(live_gcp_connector):
    params = {
        "service_name": os.environ["SMOKE_CLOUD_RUN_SERVICE"],
        "region": os.environ["SMOKE_CLOUD_RUN_REGION"],
        "image": os.environ["SMOKE_CLOUD_RUN_IMAGE"],
    }
    result = await cr_execute(params, [], live_gcp_connector)
    assert result["status"] == "deployed"
    assert result["url"]
    rb = await cr_rollback(params, result, live_gcp_connector)
    assert rb["rolled_back"] is True


@pytest.mark.smoke
@pytest.mark.skipif(not os.getenv("SMOKE_AZURE_CA_APP"), reason="SMOKE_AZURE_CA_APP not set")
async def test_azure_container_apps_deploy_and_rollback(live_azure_connector):
    params = {
        "resource_group": os.environ["SMOKE_AZURE_CA_RG"],
        "app_name": os.environ["SMOKE_AZURE_CA_APP"],
        "image": os.environ["SMOKE_AZURE_CA_IMAGE"],
    }
    result = await ca_execute(params, [], live_azure_connector)
    assert result["status"] == "deployed"
    rb = await ca_rollback(params, result, live_azure_connector)
    assert rb["rolled_back"] is True


@pytest.mark.smoke
@pytest.mark.skipif(not os.getenv("SMOKE_OCI_CI_INSTANCE_ID"), reason="SMOKE_OCI_CI_INSTANCE_ID not set")
async def test_oci_container_instances_deploy_and_rollback(live_oci_connector):
    params = {
        "compartment_id": os.environ["SMOKE_OCI_CI_COMPARTMENT"],
        "instance_id": os.environ["SMOKE_OCI_CI_INSTANCE_ID"],
        "image": os.environ["SMOKE_OCI_CI_IMAGE"],
    }
    result = await oci_execute(params, [], live_oci_connector)
    assert result["status"] == "deployed"
    rb = await oci_rollback(params, result, live_oci_connector)
    assert rb["rolled_back"] is True
