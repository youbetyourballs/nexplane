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
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Mount, Route

from app.database import AsyncSessionLocal
from app.models.api_token import ApiToken
from app.models.user import User

logger = logging.getLogger(__name__)

# ── MCP server singleton ─────────────────────────────────────────────────────

mcp = Server("nexplane")
sse = SseServerTransport("/mcp/messages")


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


async def _get_user_from_request(request: Request) -> tuple[User, Any]:
    """Extract Bearer token from request headers, validate, return (user, db)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    raw_token = auth[len("Bearer "):]
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_token(raw_token, db)
        await db.commit()
        return user, db


# ── SSE endpoint handlers ─────────────────────────────────────────────────────

async def handle_sse(request: Request):
    try:
        user, _ = await _get_user_from_request(request)
    except HTTPException as e:
        from starlette.responses import Response
        return Response(str(e.detail), status_code=e.status_code)

    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await mcp.run(
            streams[0],
            streams[1],
            mcp.create_initialization_options(),
        )


async def handle_messages(request: Request):
    await sse.handle_post_message(request.scope, request.receive, request._send)


# ── Starlette sub-app (mounted at /mcp in main.py) ──────────────────────────

def create_mcp_app() -> Starlette:
    """Return the Starlette app to mount at /mcp."""
    # Import tool modules so their @mcp.tool() decorators register
    try:
        import app.mcp_tools.findings  # noqa: F401
    except Exception:
        pass  # findings tools depend on vuln_poc_service — loaded when available
    import app.mcp_tools.change_requests  # noqa: F401

    return Starlette(routes=[
        Route("/sse", endpoint=handle_sse),
        Route("/messages", endpoint=handle_messages, methods=["POST"]),
    ])
