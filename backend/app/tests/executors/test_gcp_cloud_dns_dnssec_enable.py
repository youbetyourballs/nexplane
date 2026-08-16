# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


@pytest.fixture
def connector():
    c = MagicMock()
    c.credentials = {"service_account_key_json": "{}", "project_id": "test-proj"}
    return c


@pytest.mark.asyncio
async def test_already_enabled_returns_no_op(connector):
    call_log = []

    async def fake_run(fn):
        call_log.append(fn.__name__ if hasattr(fn, "__name__") else str(fn))
        if len(call_log) == 1:  # get_zone
            return (
                {"dnssecConfig": {"state": "on"}, "name": "example.com."},
                "test-proj",
                None,
            )
        if len(call_log) == 2:  # get_keys
            return {
                "dnsKeys": [{"type": "keySigning", "dsDigests": [{"digest": "ABCD", "type": "SHA256"}], "keyTag": 1234}],
            }
        return {}

    with patch("app.connectors.executors.gcp.gcp_cloud_dns_dnssec_enable._run", side_effect=fake_run):
        from app.connectors.executors.gcp.gcp_cloud_dns_dnssec_enable import execute
        result = await execute({"zone_name": "example.com."}, [], connector)
    assert result["already_enabled"] is True
    assert result["status"] == "signed"


@pytest.mark.asyncio
async def test_enables_dnssec(connector):
    call_log = []

    async def fake_run(fn):
        call_log.append(fn.__name__ if hasattr(fn, "__name__") else str(fn))
        if len(call_log) == 1:  # get_zone
            return (
                {"dnssecConfig": {"state": "off"}, "name": "example.com."},
                "test-proj",
                None,
            )
        if len(call_log) == 2:  # patch_enable
            return {}
        if len(call_log) == 3:  # poll
            return (
                {"dnssecConfig": {"state": "on"}, "name": "example.com."},
                {"dnsKeys": [{"type": "keySigning", "dsDigests": [{"digest": "ABCD", "type": "SHA256"}], "keyTag": 1234}]},
            )
        return {}

    with patch("app.connectors.executors.gcp.gcp_cloud_dns_dnssec_enable._run", side_effect=fake_run):
        from app.connectors.executors.gcp.gcp_cloud_dns_dnssec_enable import execute
        result = await execute({"zone_name": "example.com."}, [], connector)
    assert result["status"] == "signed"
    assert result["already_enabled"] is False
    assert "ds_records" in result
