# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest


@pytest.mark.asyncio
async def test_ldap_disable_user_skips_without_credentials():
    from app.connectors.executors.ldap.disable_user import execute
    result = await execute({"username": "testuser"}, ["asset-1"], None)
    assert result["status"] == "skipped"
    assert result["reason"] == "no_ldap_credentials"


@pytest.mark.asyncio
async def test_ldap_client_requires_host():
    from app.connectors.executors.ldap._client import get_ldap_client
    assert await get_ldap_client(None) is None
