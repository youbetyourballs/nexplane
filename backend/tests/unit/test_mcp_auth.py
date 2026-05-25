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


def test_token_schemas_importable():
    from app.schemas.api_token import TokenCreate, TokenRead, TokenCreatedResponse
    t = TokenCreate(name="test token")
    assert t.name == "test token"
