# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest

MockConnector = type("Connector", (), {"credentials": {}})
RealConnector = type("Connector", (), {"credentials": {"token": "ghp_test", "org": "my-org"}})


@pytest.mark.asyncio
async def test_remove_org_member_mock():
    from app.connectors.executors.github.remove_org_member import execute
    result = await execute({"username": "octocat"}, [], MockConnector())
    assert result["action"] == "remove_org_member"
    assert result["username"] == "octocat"


@pytest.mark.asyncio
async def test_revoke_user_pats_mock():
    from app.connectors.executors.github.revoke_user_pats import execute
    result = await execute({"username": "octocat"}, [], MockConnector())
    assert result["action"] == "revoke_user_pats"
    assert isinstance(result["pats_revoked"], list)


@pytest.mark.asyncio
async def test_enforce_branch_protection_mock():
    from app.connectors.executors.github.enforce_branch_protection import execute
    result = await execute(
        {"repo": "my-repo", "branch": "main", "require_pr_reviews": True, "enforce_admins": True},
        [],
        MockConnector(),
    )
    assert result["action"] == "enforce_branch_protection"
    assert result["repo"] == "my-repo"


@pytest.mark.asyncio
async def test_archive_repo_mock():
    from app.connectors.executors.github.archive_repo import execute
    result = await execute({"repo": "my-repo"}, [], MockConnector())
    assert result["action"] == "archive_repo"
    assert result["archived"] is True


@pytest.mark.asyncio
async def test_disable_actions_mock():
    from app.connectors.executors.github.disable_actions import execute
    result = await execute({"repo": "my-repo"}, [], MockConnector())
    assert result["action"] == "disable_actions"
    assert result["enabled"] is False


@pytest.mark.asyncio
async def test_enable_actions_mock():
    from app.connectors.executors.github.enable_actions import execute
    result = await execute({"repo": "my-repo"}, [], MockConnector())
    assert result["action"] == "enable_actions"
    assert result["enabled"] is True


@pytest.mark.asyncio
async def test_archive_repo_rollback():
    from app.connectors.executors.github.archive_repo import rollback
    result = await rollback({"repo": "my-repo"}, {"archived": True}, MockConnector())
    assert "rolled_back" in result or "action" in result
