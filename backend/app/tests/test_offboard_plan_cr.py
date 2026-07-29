# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.change_plan_service import PlanBlockedError
from app.services import change_plan_service


def _make_cr(target_email="alice@corp.com"):
    cr = MagicMock()
    cr.change_type = MagicMock()
    cr.change_type.value = "offboard_user"
    cr.change_type.__eq__ = lambda self, other: str(other) == "offboard_user" or getattr(other, "value", None) == "offboard_user"
    cr.desired_outcome = {"target_email": target_email, "reason": "termination"}
    cr.organization_id = uuid.uuid4()
    cr.id = uuid.uuid4()
    cr.target_asset_ids = []
    return cr


@pytest.mark.asyncio
async def test_run_offboard_discovery_no_connectors(monkeypatch):
    """With no matching connectors in DB, discovery returns empty list."""
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=mock_result)

    manifest = await change_plan_service._run_offboard_discovery(db, "alice@corp.com", uuid.uuid4())
    assert manifest == []


@pytest.mark.asyncio
async def test_run_offboard_discovery_returns_manifest(monkeypatch):
    """With one AD connector, discovery calls discover_accounts.execute and returns manifest."""
    db = AsyncMock()

    fake_connector = MagicMock()
    fake_connector.id = uuid.uuid4()
    fake_connector.connector_type = MagicMock()
    fake_connector.connector_type.value = "active_directory"
    fake_connector.credentials = {"base_dn": "DC=corp,DC=local"}

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [fake_connector]
    db.execute = AsyncMock(return_value=mock_result)

    async def _mock_discover(params, connector):
        return {"found": True, "account_identifier": "alice", "connector_type": "active_directory", "details": {}}

    monkeypatch.setattr(
        "app.services.change_plan_service._discover_account_on_connector",
        _mock_discover,
    )

    manifest = await change_plan_service._run_offboard_discovery(db, "alice@corp.com", fake_connector.id)
    assert len(manifest) == 1
    assert manifest[0]["found"] is True
    assert manifest[0]["account_identifier"] == "alice"


@pytest.mark.asyncio
async def test_run_offboard_discovery_handles_exception(monkeypatch):
    """If discover_accounts raises, the connector appears in manifest with found=False."""
    db = AsyncMock()

    fake_connector = MagicMock()
    fake_connector.id = uuid.uuid4()
    fake_connector.connector_type = MagicMock()
    fake_connector.connector_type.value = "okta"
    fake_connector.credentials = {}

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [fake_connector]
    db.execute = AsyncMock(return_value=mock_result)

    async def _mock_discover_error(params, connector):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(
        "app.services.change_plan_service._discover_account_on_connector",
        _mock_discover_error,
    )

    manifest = await change_plan_service._run_offboard_discovery(db, "alice@corp.com", fake_connector.id)
    assert len(manifest) == 1
    assert manifest[0]["found"] is False
    assert "error" in manifest[0]["details"]
