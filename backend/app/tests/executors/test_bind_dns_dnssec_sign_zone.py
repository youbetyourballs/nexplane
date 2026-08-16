# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


@pytest.fixture
def connector():
    c = MagicMock()
    c.credentials = {
        "host": "192.168.1.1",
        "port": 22,
        "username": "root",
        "ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\ntest\n-----END OPENSSH PRIVATE KEY-----",
    }
    return c


@pytest.mark.asyncio
async def test_signs_zone(connector):
    mock_job_result = {
        "status": "success",
        "output": "K example.com.+013+01234.key\nK example.com.+013+56789.key\nDone signing.\nDS record: example.com. 3600 IN DS 1234 13 2 ABCD1234",
    }

    with patch(
        "app.connectors.executors.bind_dns.bind_dns_dnssec_sign_zone.dispatch_agent_job",
        new=AsyncMock(return_value=mock_job_result),
    ):
        from app.connectors.executors.bind_dns.bind_dns_dnssec_sign_zone import execute
        result = await execute(
            {"zone": "example.com.", "zone_file_path": "/var/named/example.com.zone"},
            [],
            connector,
        )

    assert result["status"] == "signed"
    assert result["zone"] == "example.com."


@pytest.mark.asyncio
async def test_rollback_restores_unsigned_zone(connector):
    mock_job_result = {"status": "success", "output": ""}
    execution_result = {
        "zone": "example.com.",
        "zone_file_path": "/var/named/example.com.zone",
        "signed_zone_file": "/var/named/example.com.zone.signed",
        "key_directory": "/var/named/keys",
    }

    with patch(
        "app.connectors.executors.bind_dns.bind_dns_dnssec_sign_zone.dispatch_agent_job",
        new=AsyncMock(return_value=mock_job_result),
    ):
        from app.connectors.executors.bind_dns.bind_dns_dnssec_sign_zone import rollback
        result = await rollback({"zone": "example.com."}, execution_result, connector)

    assert result["rolled_back"] is True
