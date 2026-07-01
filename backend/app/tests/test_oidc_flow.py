# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, patch
from app.services.oidc_service import build_authorization_url, OidcConfig, exchange_code_for_userinfo


@pytest.mark.asyncio
async def test_build_authorization_url():
    cfg = OidcConfig(
        issuer="https://accounts.example.com",
        client_id="client123",
        client_secret="secret",
        scopes=["openid", "email", "profile"],
    )
    url = build_authorization_url(
        config=cfg,
        redirect_uri="https://app.example.com/auth/oidc/callback",
        state="random-state-123",
    )
    assert "response_type=code" in url
    assert "client_id=client123" in url
    assert "state=random-state-123" in url


@pytest.mark.asyncio
async def test_exchange_code_returns_userinfo():
    cfg = OidcConfig(
        issuer="https://accounts.example.com",
        client_id="client123",
        client_secret="secret",
        scopes=["openid", "email", "profile"],
    )
    mock_token_response = {"access_token": "tok", "id_token": "id.tok.sig"}
    mock_userinfo = {"sub": "1234", "email": "user@example.com", "name": "Test User"}

    with patch("app.services.oidc_service._fetch_token", new=AsyncMock(return_value=mock_token_response)), \
         patch("app.services.oidc_service._fetch_userinfo", new=AsyncMock(return_value=mock_userinfo)), \
         patch("app.services.oidc_service._get_oidc_metadata", new=AsyncMock(return_value={
             "token_endpoint": "https://accounts.example.com/token",
             "userinfo_endpoint": "https://accounts.example.com/userinfo",
         })):
        info = await exchange_code_for_userinfo(
            config=cfg,
            code="auth-code-abc",
            redirect_uri="https://app.example.com/auth/oidc/callback",
        )
    assert info["email"] == "user@example.com"
