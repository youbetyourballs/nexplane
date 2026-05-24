import asyncio, sys
sys.path.insert(0, '/app')
from app.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        r = await db.execute(text(
            "SELECT cr.title, er.status, er.result "
            "FROM change_requests cr "
            "LEFT JOIN execution_runs er ON er.change_request_id = cr.id "
            "WHERE cr.id = '94b3c892-4757-4ca1-94fb-493893d997ab'"
        ))
        for row in r:
            print('title:', row[0])
            print('er_status:', row[1])
            print('result:', str(row[2])[:600])

asyncio.run(main())
