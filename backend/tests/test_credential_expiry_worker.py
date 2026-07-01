# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_discover_api_key_consumers_finds_references():
    from app.workers.credential_expiry_worker import _discover_api_key_consumers
    import uuid

    asset = MagicMock()
    asset.id = uuid.uuid4()
    asset.name = "my-server"
    asset.asset_metadata = {"env_vars": "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"}

    mock_result = MagicMock()
    mock_result.scalars.return_value = [asset]

    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(return_value=mock_result)

    consumers = await _discover_api_key_consumers(db, "AKIAIOSFODNN7EXAMPLE", "aws_iam_key")
    assert len(consumers) == 1
    assert consumers[0]["asset_name"] == "my-server"
    assert consumers[0]["config_path"] == "env_vars"


@pytest.mark.asyncio
async def test_discover_api_key_consumers_no_match():
    from app.workers.credential_expiry_worker import _discover_api_key_consumers

    asset = MagicMock()
    asset.asset_metadata = {"env_vars": "OTHER_KEY=xyz"}

    mock_result = MagicMock()
    mock_result.scalars.return_value = [asset]

    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(return_value=mock_result)

    consumers = await _discover_api_key_consumers(db, "AKIAIOSFODNN7EXAMPLE", "aws_iam_key")
    assert consumers == []


@pytest.mark.asyncio
async def test_check_vault_leases_renews_renewable():
    from app.workers.credential_expiry_worker import _check_vault_leases

    connector = MagicMock()
    connector.id = "vault-conn-1"
    db = AsyncMock(spec=AsyncSession)

    short_ttl_lease = {"lease_id": "database/creds/my-role/abc", "ttl": 3600, "renewable": True}

    with patch("app.workers.credential_expiry_worker._get_connectors_by_type", new=AsyncMock(return_value=[connector])), \
         patch("app.connectors.executors.hashicorp_vault._client.VaultClient") as mock_cls:

        mock_client = MagicMock()
        mock_client.list_leases.return_value = [short_ttl_lease]
        mock_cls.from_connector.return_value = mock_client

        await _check_vault_leases(db)
        mock_client.renew_lease.assert_called_once_with(short_ttl_lease["lease_id"])


@pytest.mark.asyncio
async def test_check_vault_leases_creates_finding_when_not_renewable():
    from app.workers.credential_expiry_worker import _check_vault_leases

    connector = MagicMock()
    connector.id = "vault-conn-1"
    db = AsyncMock(spec=AsyncSession)

    expired_lease = {"lease_id": "pki/issue/my-role/xyz", "ttl": 3600, "renewable": False}

    with patch("app.workers.credential_expiry_worker._get_connectors_by_type", new=AsyncMock(return_value=[connector])), \
         patch("app.connectors.executors.hashicorp_vault._client.VaultClient") as mock_cls, \
         patch("app.workers.credential_expiry_worker._create_expiry_finding") as mock_finding:

        mock_client = MagicMock()
        mock_client.list_leases.return_value = [expired_lease]
        mock_cls.from_connector.return_value = mock_client

        await _check_vault_leases(db)
        mock_client.renew_lease.assert_not_called()
        mock_finding.assert_called_once()


@pytest.mark.asyncio
async def test_check_ssh_key_age_creates_finding_for_old_key():
    from app.workers.credential_expiry_worker import _check_ssh_key_age
    from datetime import datetime, timezone, timedelta

    db = AsyncMock(spec=AsyncSession)
    asset = MagicMock()
    asset.id = "asset-1"
    asset.name = "prod-server"
    asset.asset_type = "server"

    mock_result = MagicMock()
    mock_result.scalars.return_value = MagicMock()
    mock_result.scalars.return_value.all.return_value = [asset]
    db.execute = AsyncMock(return_value=mock_result)

    old_date = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d")
    fake_keys = [{"key_fingerprint": "SHA256:abc", "comment": "admin@laptop", "added_date": old_date}]

    with patch("app.workers.credential_expiry_worker._run_ssh_authorized_keys_audit", new=AsyncMock(return_value=fake_keys)), \
         patch("app.workers.credential_expiry_worker._create_expiry_finding") as mock_finding:

        await _check_ssh_key_age(db)
        mock_finding.assert_called_once()
        call_args = mock_finding.call_args[0]
        assert "admin@laptop" in call_args[3]


@pytest.mark.asyncio
async def test_check_ssh_key_age_skips_young_key():
    from app.workers.credential_expiry_worker import _check_ssh_key_age
    from datetime import datetime, timezone, timedelta

    db = AsyncMock(spec=AsyncSession)
    asset = MagicMock()
    asset.asset_type = "server"

    mock_result = MagicMock()
    mock_result.scalars.return_value = MagicMock()
    mock_result.scalars.return_value.all.return_value = [asset]
    db.execute = AsyncMock(return_value=mock_result)

    recent_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    fake_keys = [{"key_fingerprint": "SHA256:def", "comment": "ci-bot", "added_date": recent_date}]

    with patch("app.workers.credential_expiry_worker._run_ssh_authorized_keys_audit", new=AsyncMock(return_value=fake_keys)), \
         patch("app.workers.credential_expiry_worker._create_expiry_finding") as mock_finding:

        await _check_ssh_key_age(db)
        mock_finding.assert_not_called()


@pytest.mark.asyncio
async def test_check_step_ca_certs_renews_expiring():
    from app.workers.credential_expiry_worker import _check_step_ca_certs
    from datetime import datetime, timezone, timedelta

    db = AsyncMock(spec=AsyncSession)
    connector = MagicMock()
    connector.id = "stepca-1"

    expiring_cert = {
        "serial": "cert-001",
        "subject": "CN=api.example.com",
        "expiry": datetime.now(timezone.utc) + timedelta(days=20),
    }

    with patch("app.workers.credential_expiry_worker._get_connectors_by_type", new=AsyncMock(return_value=[connector])), \
         patch("app.connectors.executors.step_ca._client.StepCAClient") as mock_cls:

        mock_client = MagicMock()
        mock_client.list_certificates.return_value = [expiring_cert]
        mock_cls.from_connector.return_value = mock_client

        await _check_step_ca_certs(db)
        mock_client.renew_certificate.assert_called_once_with("cert-001")


@pytest.mark.asyncio
async def test_check_step_ca_certs_creates_finding_on_renewal_failure():
    from app.workers.credential_expiry_worker import _check_step_ca_certs
    from datetime import datetime, timezone, timedelta

    db = AsyncMock(spec=AsyncSession)
    connector = MagicMock()
    connector.id = "stepca-1"

    expiring_cert = {
        "serial": "cert-fail",
        "subject": "CN=broken.example.com",
        "expiry": datetime.now(timezone.utc) + timedelta(days=10),
    }

    with patch("app.workers.credential_expiry_worker._get_connectors_by_type", new=AsyncMock(return_value=[connector])), \
         patch("app.connectors.executors.step_ca._client.StepCAClient") as mock_cls, \
         patch("app.workers.credential_expiry_worker._create_expiry_finding") as mock_finding:

        mock_client = MagicMock()
        mock_client.list_certificates.return_value = [expiring_cert]
        mock_client.renew_certificate.side_effect = Exception("ACME error")
        mock_cls.from_connector.return_value = mock_client

        await _check_step_ca_certs(db)
        mock_finding.assert_called_once()
        finding_msg = mock_finding.call_args[0][3]
        assert "auto-renewal failed" in finding_msg
