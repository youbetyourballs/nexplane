import asyncio, sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.config import settings
from app.models.connector import Connector, ConnectorType
from app.services.connector_service import _attach_credentials

async def main():
    engine = create_async_engine(settings.DATABASE_URL)
    S = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with S() as db:
        r = await db.execute(sa.select(Connector).where(Connector.connector_type == ConnectorType.tailscale))
        conn = r.scalars().first()
        await _attach_credentials(conn, db)
        creds = getattr(conn, "credentials", {}) or {}
        for k, v in creds.items():
            print(f"{k}: {str(v)[:80]}")
    await engine.dispose()

asyncio.run(main())
