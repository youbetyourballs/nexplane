# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Account baseline hardening live smoke tests.

All three executors run against live cloud accounts.

Env vars:
  SMOKE_GCP_HARDENING_PROJECT      — GCP project ID for hardening tests
  SMOKE_AZURE_HARDENING_SUBSCRIPTION — Azure subscription ID for hardening tests
  SMOKE_OCI_HARDENING_COMPARTMENT  — OCI compartment ID for hardening tests
"""
import os
import pytest
from app.connectors.executors.gcp.gcp_account_baseline_hardening import execute as gcp_execute, rollback as gcp_rollback
from app.connectors.executors.azure.azure_account_baseline_hardening import execute as az_execute, rollback as az_rollback
from app.connectors.executors.oci.oci_account_baseline_hardening import execute as oci_execute, rollback as oci_rollback


@pytest.mark.smoke
@pytest.mark.skipif(
    not os.getenv("SMOKE_GCP_HARDENING_PROJECT"),
    reason="SMOKE_GCP_HARDENING_PROJECT not set",
)
async def test_gcp_baseline_hardening_and_rollback(live_gcp_connector):
    result = await gcp_execute({}, [], live_gcp_connector)
    assert result["phase"] == "report"
    assert result["status"] == "ok"
    rb = await gcp_rollback({}, result, live_gcp_connector)
    assert rb["rolled_back"] is True


@pytest.mark.smoke
@pytest.mark.skipif(
    not os.getenv("SMOKE_AZURE_HARDENING_SUBSCRIPTION"),
    reason="SMOKE_AZURE_HARDENING_SUBSCRIPTION not set",
)
async def test_azure_baseline_hardening_and_rollback(live_azure_connector):
    result = await az_execute({}, [], live_azure_connector)
    assert result["phase"] == "report"
    assert result["status"] == "ok"
    rb = await az_rollback({}, result, live_azure_connector)
    assert rb["rolled_back"] is True


@pytest.mark.smoke
@pytest.mark.skipif(
    not os.getenv("SMOKE_OCI_HARDENING_COMPARTMENT"),
    reason="SMOKE_OCI_HARDENING_COMPARTMENT not set",
)
async def test_oci_baseline_hardening_and_rollback(live_oci_connector):
    result = await oci_execute({}, [], live_oci_connector)
    assert result["phase"] == "report"
    assert result["status"] == "ok"
    rb = await oci_rollback({}, result, live_oci_connector)
    assert rb["rolled_back"] is True
