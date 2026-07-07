#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""One-shot script: print GCP SA info + list GCS buckets."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile

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
    print("GCP cred keys:", list(creds.keys()))

    sa_raw = creds.get("service_account_key_json", "")
    if not sa_raw:
        print("No service_account_key_json in creds")
        return

    sa = json.loads(sa_raw) if isinstance(sa_raw, str) else sa_raw
    project_id = sa.get("project_id", "")
    client_email = sa.get("client_email", "")
    print(f"project_id: {project_id}")
    print(f"client_email: {client_email}")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sa, f)
        key_path = f.name

    try:
        env = {**os.environ, "GOOGLE_APPLICATION_CREDENTIALS": key_path}
        # List GCS buckets
        r2 = subprocess.run(
            ["gcloud", "storage", "buckets", "list",
             f"--project={project_id}", "--format=value(name)"],
            env=env, capture_output=True, text=True, timeout=30,
        )
        print("=== GCS buckets ===")
        print(r2.stdout[:1000] or "(none)")
        if r2.stderr:
            print("stderr:", r2.stderr[:500])
    finally:
        os.unlink(key_path)


asyncio.run(main())
