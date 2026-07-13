# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, patch

SAMPLE_PROFILE = {
    "processes": [{"name": "nginx", "pid": 100, "executable": "/usr/sbin/nginx",
                   "service_unit": "nginx.service", "listening_ports": [80],
                   "outbound_connections": [], "linked_libraries": []}],
    "endpoints": [{"protocol": "http", "port": 80, "process": "nginx",
                   "declared_health_path": "/health", "source": "runtime"}],
    "dependencies": [{"type": "db", "host": "localhost", "port": 5432,
                      "dsn_template": "postgresql://localhost:5432/app",
                      "source": "config", "confidence": "both"}],
    "config_files": [{"path": "/etc/app/config.ini", "format": "ini",
                      "extracted_keys": ["database_url"]}],
    "library_versions": [{"name": "libpq", "version": "14.0", "path": "/usr/lib/libpq.so"}],
}


@pytest.mark.asyncio
async def test_execute_returns_auto_asset():
    with patch(
        "app.connectors.executors.nexplane_agent.discover_application_profile._dispatch.dispatch_agent_job",
        new_callable=AsyncMock,
        return_value={"profile": SAMPLE_PROFILE, "hostname": "smoke-host"},
    ):
        from app.connectors.executors.nexplane_agent import discover_application_profile as m

        result = await m.execute(
            parameters={"asset_id": "abc123"},
            asset_ids=["abc123"],
            connector=None,
        )

    assert result["action"] == "discover_application_profile"
    assert "_auto_asset" in result
    assert result["_auto_asset"]["asset_type"] == "application_profile"
    assert "application_profile" in result["_auto_asset"]["name"].lower()
    assert result["profile"]["endpoints"][0]["port"] == 80


@pytest.mark.asyncio
async def test_rollback_is_noop():
    from app.connectors.executors.nexplane_agent import discover_application_profile as m

    result = await m.rollback(parameters={}, execution_result={}, connector=None)
    assert result["rolled_back"] is False
    assert "non-mutating" in result["reason"]
