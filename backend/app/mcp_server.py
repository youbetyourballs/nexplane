"""
Nexplane MCP Server.

Mounts at /mcp via SSE transport inside the existing FastAPI process.
All tools authenticate via API token (Authorization: Bearer nxp_<token>).
Token is role-mapped to its generating User — existing RBAC applies unchanged.
"""
from __future__ import annotations
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.api_token import ApiToken
from app.models.user import User

logger = logging.getLogger(__name__)

# ── MCP server singleton ─────────────────────────────────────────────────────

mcp = FastMCP("nexplane")


# ── Token auth ───────────────────────────────────────────────────────────────

async def _lookup_api_token(db, token_hash: str):
    result = await db.execute(
        select(ApiToken).where(
            ApiToken.token_hash == token_hash,
            ApiToken.revoked == False,
        )
    )
    return result.scalar_one_or_none()


async def _lookup_agent_token(db, token_hash: str):
    from app.models.agent_token import AgentToken

    result = await db.execute(
        select(AgentToken).where(AgentToken.token_hash == token_hash)
    )
    token = result.scalar_one_or_none()
    if not token:
        return None
    if token.revoked:
        return None
    if token.expires_at and token.expires_at < datetime.now(timezone.utc):
        return None
    return token


async def resolve_mcp_token(raw_token: str, db) -> tuple:
    """
    Validate a raw API or Agent token.
    Returns (user, None) for ApiToken or (None, agent_token) for AgentToken.
    Raises HTTPException(401) if invalid, revoked, or expired.
    """
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    api_token = await _lookup_api_token(db, token_hash)
    if api_token is not None:
        if api_token.expires_at and api_token.expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="API token expired")

        user_result = await db.execute(select(User).where(User.id == api_token.user_id))
        user = user_result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=401, detail="Token user not found")

        api_token.last_used_at = datetime.now(timezone.utc)
        return user, None

    agent_token = await _lookup_agent_token(db, token_hash)
    if agent_token:
        return None, agent_token

    raise HTTPException(status_code=401, detail="Invalid or revoked API token")


# ── Starlette sub-app (mounted at /mcp in main.py) ──────────────────────────

def create_mcp_app():
    """Return the Starlette app to mount at /mcp."""
    # Import tool modules so their @mcp.tool() decorators register
    import app.mcp_tools.findings  # noqa: F401
    import app.mcp_tools.change_requests  # noqa: F401
    import app.mcp_tools.assets  # noqa: F401
    import app.mcp_tools.connectors  # noqa: F401
    import app.mcp_tools.identity  # noqa: F401
    import app.mcp_tools.runbooks  # noqa: F401

    return mcp.sse_app()
