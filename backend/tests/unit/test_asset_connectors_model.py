# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from app.models.asset import Asset, asset_connectors_table
from app.models.connector import Connector


def test_asset_connectors_table_exists():
    assert asset_connectors_table is not None
    col_names = {c.name for c in asset_connectors_table.columns}
    assert "asset_id" in col_names
    assert "connector_id" in col_names
    assert "created_at" in col_names


def test_asset_has_connectors_relationship():
    assert hasattr(Asset, "connectors")
