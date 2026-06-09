from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, func

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.user import User

_EXEMPT_PREFIXES = ("/setup", "/health", "/docs", "/redoc", "/openapi.json", "/mcp", "/api/health", "/api/v1/setup")


class SetupGuardMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if settings.NEXPLANE_EDITION != "commercial":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "/")
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(func.count()).select_from(User))
            user_count = result.scalar_one()

        if user_count == 0:
            response = RedirectResponse(url="/setup", status_code=307)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
