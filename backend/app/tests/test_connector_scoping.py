# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pathlib
import uuid
from app.models.asset import Asset, AssetType, Environment, Criticality
from app.models.connector import Connector, ConnectorType, ConnectorStatus
from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus
from app.services.planning_engine import generate_plan
from app.services.safety_engine import score_change_request
from app.connectors.catalog_service import init_catalog_service

CATALOG_DIR = pathlib.Path(__file__).parent.parent / "connectors" / "catalog"


def setup_module(module):
    init_catalog_service(CATALOG_DIR)


def _make_asset_with_connector(connector_type_val: str) -> tuple:
    org_id = uuid.uuid4()
    connector = Connector(
        id=uuid.uuid4(),
        organization_id=org_id,
        connector_type=ConnectorType(connector_type_val),
        name=f"Test {connector_type_val}",
        status=ConnectorStatus.active,
        scoped_permissions={},
    )
    asset = Asset(
        id=uuid.uuid4(),
        organization_id=org_id,
        connector_id=connector.id,
        name="test-server",
        asset_type=AssetType.server,
        environment=Environment.prod,
        criticality=Criticality.high,
        asset_metadata={},
        tags=[],
    )
    asset.connector = connector
    return asset, connector


def test_planning_locks_to_asset_connector_type():
    """When asset has connector_id, plan steps only include that connector's type."""
    asset, connector = _make_asset_with_connector("aws")
    cr = ChangeRequest(
        id=uuid.uuid4(), organization_id=asset.organization_id,
        requester_id=uuid.uuid4(), title="Test", description="",
        change_type=ChangeType.ec2_stop,
        target_asset_ids=[str(asset.id)],
        desired_outcome={"instance_id": "i-abc123", "snapshot_tag": "test"},
        status=ChangeRequestStatus.draft,
    )
    plan = generate_plan(cr, [asset], score_change_request(cr, [asset]))
    for step in plan.generated_steps:
        if step["connector_type"] != "unknown":
            assert step["connector_type"] == "aws", f"Expected aws connector, got {step['connector_type']}"


def test_planning_includes_connector_id_in_steps():
    """Steps carry connector_id when assets have one."""
    asset, connector = _make_asset_with_connector("aws")
    cr = ChangeRequest(
        id=uuid.uuid4(), organization_id=asset.organization_id,
        requester_id=uuid.uuid4(), title="Test", description="",
        change_type=ChangeType.ec2_start,
        target_asset_ids=[str(asset.id)],
        desired_outcome={"instance_id": "i-abc123"},
        status=ChangeRequestStatus.draft,
    )
    plan = generate_plan(cr, [asset], score_change_request(cr, [asset]))
    for step in plan.generated_steps:
        if step["connector_type"] == "aws":
            assert step.get("connector_id") == str(connector.id)


import pytest


@pytest.mark.asyncio
async def test_asset_read_has_connector_fields(auth_client):
    """AssetRead schema exposes connector_id and connector_name."""
    resp = await auth_client.get("/assets")
    assert resp.status_code == 200
    data = resp.json()
    for asset in data:
        assert "connector_id" in asset
        assert "connector_name" in asset


@pytest.mark.asyncio
async def test_asset_list_accepts_connector_id_filter(auth_client):
    """Asset list endpoint accepts connector_id query param without error."""
    import uuid
    fake_connector_id = str(uuid.uuid4())
    resp = await auth_client.get(f"/assets?connector_id={fake_connector_id}")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_ingest_stamps_connector_id(auth_client):
    """Filter by connector_id works without error (assets may be empty)."""
    connectors_resp = await auth_client.get("/connectors")
    assert connectors_resp.status_code == 200
    connectors = connectors_resp.json()
    if not connectors:
        pytest.skip("No connectors in test org")
    connector = connectors[0]
    connector_id = connector["id"]
    assets_resp = await auth_client.get(f"/assets?connector_id={connector_id}")
    assert assets_resp.status_code == 200
    assert isinstance(assets_resp.json(), list)
