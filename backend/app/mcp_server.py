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

async def resolve_mcp_token(raw_token: str, db) -> User:
    """
    Validate a raw API token and return the linked User.
    Raises HTTPException(401) if invalid, revoked, or expired.
    """
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    result = await db.execute(
        select(ApiToken).where(
            ApiToken.token_hash == token_hash,
            ApiToken.revoked == False,
        )
    )
    api_token = result.scalar_one_or_none()
    if api_token is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked API token")
    if api_token.expires_at and api_token.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="API token expired")

    user_result = await db.execute(select(User).where(User.id == api_token.user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="Token user not found")

    # Update last_used_at without blocking the request
    api_token.last_used_at = datetime.now(timezone.utc)

    return user


# ── Starlette sub-app (mounted at /mcp in main.py) ──────────────────────────

def create_mcp_app():
    """Return the Starlette app to mount at /mcp."""
    # Import tool modules so their @mcp.tool() decorators register
    try:
        import app.mcp_tools.findings  # noqa: F401
    except Exception:
        pass  # findings tools depend on vuln_poc_service — loaded when available
    import app.mcp_tools.change_requests  # noqa: F401

    return mcp.sse_app()
