# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest


@pytest.mark.asyncio
async def test_gcp_discover_compute_instances_mock():
    from app.connectors.executors.gcp.discover_compute_instances import execute
    result = await execute({}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "discover_compute_instances"
    assert isinstance(result["instances"], list)


@pytest.mark.asyncio
async def test_runzero_discover_assets_mock():
    from app.connectors.executors.runzero.discover_assets import execute
    result = await execute({}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "discover_assets"
    assert result["count"] >= 0


@pytest.mark.asyncio
async def test_wiz_discover_cloud_resources_mock():
    from app.connectors.executors.wiz.discover_cloud_resources import execute
    result = await execute({}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "discover_cloud_resources"


@pytest.mark.asyncio
async def test_entra_id_disable_user_mock():
    from app.connectors.executors.entra_id.disable_user import execute
    result = await execute({"user_id": "test-user"}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "disable_user"
    assert result["account_enabled"] is False


@pytest.mark.asyncio
async def test_entra_id_block_sign_in_rollback():
    from app.connectors.executors.entra_id.block_sign_in import rollback
    result = await rollback({"user_id": "test-user"}, {}, type("C", (), {"credentials": {}})())
    assert result["action"] == "enable_user"
