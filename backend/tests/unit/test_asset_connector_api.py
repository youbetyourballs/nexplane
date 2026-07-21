# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.schemas.asset import ConnectorSummary, AssetConnectorAdd, AssetRead
import uuid


def test_connector_summary_schema():
    cs = ConnectorSummary(id=uuid.uuid4(), name="aws-prod", connector_type="aws")
    assert cs.connector_type == "aws"


def test_asset_connector_add_schema():
    body = AssetConnectorAdd(connector_id=uuid.uuid4())
    assert body.connector_id is not None


def test_asset_read_has_connectors_field():
    fields = AssetRead.model_fields
    assert "connectors" in fields
