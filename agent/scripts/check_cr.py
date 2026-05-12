import asyncio, sys, sqlalchemy as sa, json
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, selectinload
from app.config import settings
from app.models.change_request import ChangeRequest

CR_ID = sys.argv[1] if len(sys.argv) > 1 else "293d5795-cf20-4428-9739-4c27a2470c92"

async def main():
    engine = create_async_engine(settings.DATABASE_URL)
    S = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with S() as db:
        r = await db.execute(
            sa.select(ChangeRequest)
            .where(ChangeRequest.id == sa.cast(CR_ID, sa.UUID))
            .options(selectinload(ChangeRequest.execution_runs))
        )
        cr = r.scalar_one_or_none()
        if cr:
            print(f"Status: {cr.status}")
            for run in cr.execution_runs:
                print(json.dumps(run.result, indent=2)[:3000])
        else:
            print("CR not found")
    await engine.dispose()

asyncio.run(main())
