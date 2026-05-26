import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession


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
