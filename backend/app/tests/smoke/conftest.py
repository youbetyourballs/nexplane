# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Smoke test fixtures for live infrastructure connectors.

These fixtures load live connector credentials from the database
and are only available when SMOKE_* env vars are set.
"""

import os
import pytest
import pytest_asyncio
from unittest.mock import MagicMock


@pytest_asyncio.fixture
async def live_gcp_connector():
    """Load live GCP connector credentials from environment or database.

    Requires: SMOKE_GCP_PROJECT_ID and SMOKE_GCP_SERVICE_ACCOUNT_JSON env vars,
    or a live connector record in the database.
    """
    # Check for required credential env vars
    if not os.getenv("SMOKE_GCP_PROJECT_ID"):
        pytest.skip("SMOKE_GCP_PROJECT_ID not set — live GCP connector unavailable")
    if not os.getenv("SMOKE_GCP_SERVICE_ACCOUNT_JSON"):
        pytest.skip("SMOKE_GCP_SERVICE_ACCOUNT_JSON not set — live GCP connector unavailable")

    # For smoke tests, we expect credentials to be passed via env vars or database
    # This fixture returns a mock connector with credentials loaded
    connector = MagicMock()

    # In live smoke, this would fetch from database or use env vars
    # For now, return a connector object that has the expected interface
    service_account_json = os.getenv("SMOKE_GCP_SERVICE_ACCOUNT_JSON")
    connector.credentials = {
        "service_account_key_json": service_account_json,
        "project_id": os.getenv("SMOKE_GCP_PROJECT_ID"),
    }
    return connector


@pytest_asyncio.fixture
async def live_oci_connector():
    """Load live OCI connector credentials from environment or database.

    Requires: SMOKE_OCI_CONFIG_FILE, SMOKE_OCI_TENANCY_ID, SMOKE_OCI_USER_ID,
    SMOKE_OCI_FINGERPRINT, SMOKE_OCI_KEY_FILE env vars, or a live connector record.
    """
    # Check for required credential env vars
    if not os.getenv("SMOKE_OCI_USER_OCID"):
        pytest.skip("SMOKE_OCI_USER_OCID not set — live OCI connector unavailable")

    connector = MagicMock()

    # OCI credentials can come from config file or environment
    connector.credentials = {
        "config_file_path": os.getenv("SMOKE_OCI_CONFIG_FILE"),
        "tenancy_id": os.getenv("SMOKE_OCI_TENANCY_ID"),
        "user_id": os.getenv("SMOKE_OCI_USER_ID"),
        "fingerprint": os.getenv("SMOKE_OCI_FINGERPRINT"),
        "key_file_path": os.getenv("SMOKE_OCI_KEY_FILE"),
    }
    return connector


@pytest_asyncio.fixture
async def live_bind_connector():
    """Load live BIND connector credentials from environment or database.

    Requires: SMOKE_BIND_HOST, SMOKE_BIND_USERNAME, SMOKE_BIND_PRIVATE_KEY_PATH
    env vars, or a live connector record.
    """
    # Check for required credential env vars
    if not os.getenv("SMOKE_BIND_HOST"):
        pytest.skip("SMOKE_BIND_HOST not set — live BIND connector unavailable")

    connector = MagicMock()

    # BIND connector uses SSH credentials
    connector.credentials = {
        "host": os.getenv("SMOKE_BIND_HOST"),
        "username": os.getenv("SMOKE_BIND_USERNAME", "root"),
        "private_key_path": os.getenv("SMOKE_BIND_PRIVATE_KEY_PATH", "~/.ssh/id_ed25519"),
        "port": int(os.getenv("SMOKE_BIND_PORT", "22")),
    }
    return connector


@pytest_asyncio.fixture
async def live_azure_connector():
    """Load live Azure connector credentials from environment or database.

    Requires: SMOKE_AKS_CLUSTER (to indicate Azure is configured), plus the platform
    must have Azure credentials set up. The actual credentials (tenant_id, client_id,
    client_secret, subscription_id) are loaded from env vars or platform database.
    """
    # Check for required env vars
    if not os.getenv("SMOKE_AKS_CLUSTER"):
        pytest.skip("SMOKE_AKS_CLUSTER not set — live Azure connector unavailable")

    connector = MagicMock()

    # Azure credentials: expect these to be set in the environment
    # In practice, these come from platform database or CI env vars
    connector.credentials = {
        "tenant_id": os.getenv("AZURE_TENANT_ID", ""),
        "client_id": os.getenv("AZURE_CLIENT_ID", ""),
        "client_secret": os.getenv("AZURE_CLIENT_SECRET", ""),
        "subscription_id": os.getenv("AZURE_SUBSCRIPTION_ID", ""),
    }

    # Validate that credentials are available
    if not all(connector.credentials.values()):
        pytest.skip("Azure credentials not fully configured (AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_SUBSCRIPTION_ID)")

    return connector
