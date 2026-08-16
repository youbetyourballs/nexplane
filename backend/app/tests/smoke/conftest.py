# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Smoke test fixtures for live infrastructure connectors.

Credentials are loaded from the platform database. Each fixture skips
unless the corresponding SMOKE_*_CONNECTOR_ID env var is set or the
default connector ID is used.

Default connector IDs match the live platform deployment:
  AWS:   666e237d-bfcf-43a5-ae24-f1a0b4f2c5cc
  GCP:   c91d563c-6098-493d-8706-215397705e64
  Azure: 356cc5eb-ad59-4eda-8014-2f4fddb7f768
  OCI:   0b3cf029-5ca0-4794-b982-6f494aaca372
"""

import os
import pytest
import pytest_asyncio
from unittest.mock import MagicMock

_DEFAULT_AWS_ID = "666e237d-bfcf-43a5-ae24-f1a0b4f2c5cc"
_DEFAULT_GCP_ID = "c91d563c-6098-493d-8706-215397705e64"
_DEFAULT_AZURE_ID = "356cc5eb-ad59-4eda-8014-2f4fddb7f768"
_DEFAULT_OCI_ID = "0b3cf029-5ca0-4794-b982-6f494aaca372"


def _load_creds_sync(connector_id: str) -> dict:
    """Load connector credentials synchronously via psycopg2 + secret backend."""
    import json
    import os as _os
    import psycopg2
    from app.services.secret_backend_factory import get_secret_backend

    db_url = _os.getenv("DATABASE_URL", "")
    # Convert async URL to sync
    sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

    conn = psycopg2.connect(sync_url)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT credentials_encrypted FROM connector_credentials WHERE connector_id = %s",
            (connector_id,),
        )
        row = cur.fetchone()
        if not row:
            return {}
        return get_secret_backend().decrypt_json(row[0]) or {}
    finally:
        conn.close()


@pytest.fixture
def live_aws_connector():
    connector_id = os.getenv("SMOKE_AWS_CONNECTOR_ID", _DEFAULT_AWS_ID)
    creds = _load_creds_sync(connector_id)
    if not creds.get("access_key_id") or not creds.get("secret_access_key"):
        pytest.skip("AWS connector credentials not found in database")
    connector = MagicMock()
    connector.credentials = creds
    return connector


@pytest.fixture
def live_gcp_connector():
    connector_id = os.getenv("SMOKE_GCP_CONNECTOR_ID", _DEFAULT_GCP_ID)
    creds = _load_creds_sync(connector_id)
    if not creds.get("service_account_key_json") or not creds.get("project_id"):
        pytest.skip("GCP connector credentials not found in database")
    connector = MagicMock()
    connector.credentials = creds
    return connector


@pytest.fixture
def live_azure_connector():
    connector_id = os.getenv("SMOKE_AZURE_CONNECTOR_ID", _DEFAULT_AZURE_ID)
    creds = _load_creds_sync(connector_id)
    if not creds.get("tenant_id") or not creds.get("client_id") or not creds.get("client_secret"):
        pytest.skip("Azure connector credentials not found in database")
    connector = MagicMock()
    connector.credentials = creds
    return connector


@pytest.fixture
def live_oci_connector():
    connector_id = os.getenv("SMOKE_OCI_CONNECTOR_ID", _DEFAULT_OCI_ID)
    creds = _load_creds_sync(connector_id)
    if not creds.get("tenancy") or not creds.get("user") or not creds.get("private_key"):
        pytest.skip("OCI connector credentials not found in database")
    connector = MagicMock()
    connector.credentials = creds
    return connector


@pytest.fixture
def live_bind_connector():
    if not os.getenv("SMOKE_BIND_HOST"):
        pytest.skip("SMOKE_BIND_HOST not set — live BIND connector unavailable")
    connector = MagicMock()
    connector.credentials = {
        "host": os.getenv("SMOKE_BIND_HOST"),
        "username": os.getenv("SMOKE_BIND_USERNAME", "root"),
        "private_key": os.getenv("SMOKE_BIND_PRIVATE_KEY", ""),
        "port": int(os.getenv("SMOKE_BIND_PORT", "22")),
    }
    return connector
