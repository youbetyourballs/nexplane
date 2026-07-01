# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

#!/usr/bin/env python3
"""Run inside Docker container to print GitHub token from platform DB."""
import asyncio
import sys

sys.path.insert(0, "/app")
from app.config import settings
from app.models.connector import Connector, ConnectorType
from app.services.connector_service import _attach_credentials
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


async def main():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    S = sessionmaker(engine, class_=AsyncSession)
    async with S() as db:
        r = await db.execute(
            sa.select(Connector).where(Connector.connector_type == ConnectorType.github)
        )
        conn = r.scalars().first()
        if not conn:
            print("TOKEN=")
            return
        await _attach_credentials(conn, db)
        creds = getattr(conn, "credentials", {})
        token = (
            creds.get("token")
            or creds.get("api_key")
            or creds.get("personal_access_token")
            or creds.get("access_token", "")
        )
        print("TOKEN=" + token)


asyncio.run(main())
