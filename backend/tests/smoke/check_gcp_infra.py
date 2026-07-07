#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""One-shot script: print GCP SA info + list GCS buckets using Python client."""
import asyncio
import json
import sys

sys.path.insert(0, "/app")


async def main():
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector
    from app.services.connector_service import _attach_credentials
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(Connector).where(Connector.connector_type == "gcp")
        )
        conn = r.scalars().first()
        if not conn:
            print("NO GCP CONNECTOR FOUND")
            return
        await _attach_credentials(conn, db)
        creds = getattr(conn, "credentials", {}) or {}

    print("GCP connector name:", conn.name)
    sa_raw = creds.get("service_account_key_json", "")
    if not sa_raw:
        print("No service_account_key_json")
        return

    sa = json.loads(sa_raw) if isinstance(sa_raw, str) else sa_raw
    project_id = sa.get("project_id", "")
    print(f"project_id: {project_id}")
    print(f"client_email: {sa.get('client_email')}")

    # List buckets using Python client
    from google.cloud import storage
    from google.oauth2 import service_account

    google_creds = service_account.Credentials.from_service_account_info(
        sa, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    client = storage.Client(credentials=google_creds, project=project_id)

    print("=== GCS buckets in project ===")
    buckets = list(client.list_buckets())
    if not buckets:
        print("  (none — no buckets in project)")
    for b in buckets:
        print(f"  {b.name}")


asyncio.run(main())
