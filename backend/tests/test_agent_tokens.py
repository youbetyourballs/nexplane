import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException


def test_agent_token_model_exists():
    from app.models.agent_token import AgentToken
    token = AgentToken()
    assert hasattr(token, "id")
    assert hasattr(token, "token_hash")
    assert hasattr(token, "allowed_connector_types")
    assert hasattr(token, "allowed_asset_tags")
    assert hasattr(token, "allowed_cr_types")
    assert hasattr(token, "allowed_roles")
    assert hasattr(token, "revoked")
    assert hasattr(token, "expires_at")


def test_agent_token_schemas():
    from app.schemas.agent_token import AgentTokenCreate, AgentTokenResponse
    import uuid
    from datetime import datetime

    create = AgentTokenCreate(
        name="Claude Code test",
        expires_in_days=90,
        allowed_connector_types=["aws"],
        allowed_asset_tags=["env:staging"],
        allowed_cr_types=["patch_packages"],
        allowed_roles=["read", "write"],
    )
    assert create.name == "Claude Code test"
    assert create.expires_in_days == 90

    resp = AgentTokenResponse(
        id=uuid.uuid4(),
        name="test",
        created_at=datetime.utcnow(),
        expires_at=None,
        revoked=False,
        last_used_at=None,
        allowed_connector_types=[],
        allowed_asset_tags=[],
        allowed_cr_types=[],
        allowed_roles=["read"],
    )
    assert resp.revoked is False


def test_enforce_agent_scope_passes_for_allowed():
    from app.mcp_tools.context import _enforce_agent_scope
    agent_token = MagicMock()
    agent_token.allowed_roles = ["read", "write"]
    agent_token.allowed_connector_types = ["aws"]
    agent_token.allowed_asset_tags = []
    agent_token.allowed_cr_types = ["patch_packages"]
    _enforce_agent_scope(agent_token, connector_type="aws", cr_type="patch_packages", required_role="write")


def test_enforce_agent_scope_blocks_wrong_connector():
    from app.mcp_tools.context import _enforce_agent_scope
    agent_token = MagicMock()
    agent_token.allowed_roles = ["read", "write"]
    agent_token.allowed_connector_types = ["aws"]
    agent_token.allowed_asset_tags = []
    agent_token.allowed_cr_types = []

    with pytest.raises(HTTPException) as exc_info:
        _enforce_agent_scope(agent_token, connector_type="ssh", required_role="write")
    assert exc_info.value.status_code == 403


def test_enforce_agent_scope_blocks_missing_role():
    from app.mcp_tools.context import _enforce_agent_scope
    agent_token = MagicMock()
    agent_token.allowed_roles = ["read"]
    agent_token.allowed_connector_types = []
    agent_token.allowed_asset_tags = []
    agent_token.allowed_cr_types = []

    with pytest.raises(HTTPException) as exc_info:
        _enforce_agent_scope(agent_token, required_role="write")
    assert exc_info.value.status_code == 403


def test_enforce_agent_scope_none_token_passes():
    from app.mcp_tools.context import _enforce_agent_scope
    _enforce_agent_scope(None, connector_type="ssh", required_role="approve")
