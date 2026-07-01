# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest


@pytest.mark.asyncio
async def test_sentinelone_isolate_endpoint_mock():
    from app.connectors.executors.sentinelone.isolate_endpoint import execute
    result = await execute({"agent_id": "mock-agent"}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "isolate_endpoint"
    assert result["isolated"] is True


@pytest.mark.asyncio
async def test_sentinelone_isolate_rollback():
    from app.connectors.executors.sentinelone.isolate_endpoint import rollback
    result = await rollback({"agent_id": "mock-agent"}, {}, type("C", (), {"credentials": {}})())
    assert result["action"] == "reconnect_endpoint"


@pytest.mark.asyncio
async def test_vault_discover_secret_engines_mock():
    from app.connectors.executors.hashicorp_vault.discover_secret_engines import execute
    result = await execute({}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "discover_secret_engines"
    assert isinstance(result["engines"], list)


@pytest.mark.asyncio
async def test_defender_discover_machines_mock():
    from app.connectors.executors.defender_endpoint.discover_machines import execute
    result = await execute({}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "discover_machines"
    assert result["count"] >= 0
