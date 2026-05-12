import asyncio, sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.config import settings
from app.models.asset import Asset

async def main():
    engine = create_async_engine(settings.DATABASE_URL)
    S = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with S() as db:
        r = await db.execute(sa.select(Asset).where(Asset.name.ilike("%demo%")))
        assets = r.scalars().all()
        if not assets:
            print("No demo assets found")
        for a in assets:
            meta = a.asset_metadata or {}
            print(f"{a.id} | {a.name} | {a.asset_type} | instance_id={meta.get('instance_id', 'N/A')}")
    await engine.dispose()

asyncio.run(main())
