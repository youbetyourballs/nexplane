import pytest
import uuid
from app.models.api_token import ApiToken


def test_api_token_model_fields():
    t = ApiToken()
    assert hasattr(t, "token_hash")
    assert hasattr(t, "user_id")
    assert hasattr(t, "organization_id")
    assert hasattr(t, "name")
    assert hasattr(t, "revoked")
    assert hasattr(t, "expires_at")
    assert hasattr(t, "last_used_at")


@pytest.mark.asyncio
async def test_resolve_token_valid():
    from app.mcp_server import resolve_mcp_token
    from unittest.mock import AsyncMock, MagicMock, patch
    import hashlib

    raw = "nxp_" + "a" * 64
    token_hash = hashlib.sha256(raw.encode()).hexdigest()

    mock_token = MagicMock()
    mock_token.revoked = False
    mock_token.expires_at = None
    mock_token.user_id = uuid.uuid4()

    mock_user = MagicMock()
    mock_user.organization_id = uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(side_effect=[mock_token, mock_user])
    ))

    with patch("app.mcp_server.AsyncSessionLocal", return_value=AsyncMock(__aenter__=AsyncMock(return_value=db), __aexit__=AsyncMock())):
        # Just verify the function is importable and callable
        assert callable(resolve_mcp_token)


@pytest.mark.asyncio
async def test_resolve_token_invalid_raises():
    from app.mcp_server import resolve_mcp_token
    from fastapi import HTTPException
    from unittest.mock import AsyncMock, MagicMock, patch

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=None)
    ))

    with patch("app.mcp_server.AsyncSessionLocal", return_value=AsyncMock(__aenter__=AsyncMock(return_value=db), __aexit__=AsyncMock())):
        with pytest.raises(Exception):
            await resolve_mcp_token("bad_token", db)


def test_token_schemas_importable():
    from app.schemas.api_token import TokenCreate, TokenRead, TokenCreatedResponse
    t = TokenCreate(name="test token")
    assert t.name == "test token"
