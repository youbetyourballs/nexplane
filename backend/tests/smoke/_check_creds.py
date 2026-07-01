# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio, sys, json
sys.path.insert(0, '/app')
from app.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(text(
            "SELECT connector_type, encrypted_credentials FROM connectors "
            "WHERE connector_type IN ('aws','tailscale')"
        ))
        for row in result:
            creds = row[1] or {}
            if isinstance(creds, str):
                creds = json.loads(creds)
            print(row[0], list(creds.keys()))

asyncio.run(main())
