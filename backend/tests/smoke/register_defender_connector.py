#!/usr/bin/env python3
"""Register a defender_endpoint connector using the same credentials as the azure connector."""
import asyncio
import sys
import os

sys.path.insert(0, "/app")
os.chdir("/app")

from app.services.secret_backend_factory import get_secret_backend
from app.database import engine
from sqlalchemy import text
import uuid
from datetime import datetime, timezone


async def main():
    async with engine.begin() as conn:
        # Get azure creds
        row = await conn.execute(text(
            "SELECT cc.credentials_encrypted, c.organization_id "
            "FROM connectors c JOIN connector_credentials cc ON cc.connector_id=c.id "
            "WHERE c.connector_type::text='azure' LIMIT 1"
        ))
        r = row.one_or_none()
        if not r:
            print("ERROR: No azure connector with credentials found")
            return
        enc, org_id = r

    creds = get_secret_backend().decrypt_json(enc)
    print(f"Got creds for tenant={creds.get('tenant_id', '')[:8]}... client={creds.get('client_id', '')[:8]}...")

    # Re-encrypt for defender connector (new connector_id)
    defender_conn_id = uuid.uuid4()
    new_enc = get_secret_backend().encrypt_json(creds, connector_id=str(defender_conn_id))

    async with engine.begin() as conn:
        # Check if already registered
        existing = await conn.execute(text(
            "SELECT id FROM connectors WHERE connector_type::text='defender_endpoint' AND organization_id=:org LIMIT 1"
        ), {"org": org_id})
        ex = existing.scalar_one_or_none()
        if ex:
            print(f"defender_endpoint connector already exists: {ex}")
            return

        # Create connector
        await conn.execute(text(
            "INSERT INTO connectors (id, organization_id, connector_type, name, created_at) "
            "VALUES (:id, :org, 'defender_endpoint', 'Microsoft Defender for Endpoint', :now)"
        ), {"id": str(defender_conn_id), "org": org_id, "now": datetime.now(timezone.utc)})

        # Store credentials
        await conn.execute(text(
            "INSERT INTO connector_credentials (id, connector_id, organization_id, credentials_encrypted, updated_by, updated_at) "
            "VALUES (:id, :conn_id, :org, :enc, NULL, :now)"
        ), {
            "id": str(uuid.uuid4()),
            "conn_id": str(defender_conn_id),
            "org": org_id,
            "enc": new_enc,
            "now": datetime.now(timezone.utc),
        })

    print(f"defender_endpoint connector registered: {defender_conn_id}")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
