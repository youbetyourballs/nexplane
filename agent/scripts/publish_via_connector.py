#!/usr/bin/env python3
"""Publish agent binary to S3 using Nexplane AWS connector credentials.
Run inside backend container: python /tmp/publish_via_connector.py 0.2.0
"""
import asyncio
import os
import sys

BUCKET = "nexplane-agent-downloads"
BINARY_PATH = "/tmp/nexplane-agent-linux-amd64"
VERSION = sys.argv[1] if len(sys.argv) > 1 else "0.2.0"


async def get_creds():
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from app.config import settings
    from app.models.connector import Connector, ConnectorType
    from app.services.connector_service import _attach_credentials

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with Session() as db:
            r = await db.execute(
                sa.select(Connector).where(Connector.connector_type == ConnectorType.aws)
            )
            conn = r.scalars().first()
            if not conn:
                raise RuntimeError("No AWS connector found in Nexplane database")
            await _attach_credentials(conn, db)
            creds = getattr(conn, "credentials", {}) or {}
            if not creds.get("access_key_id"):
                raise RuntimeError("AWS connector has no access_key_id")
            return creds
    finally:
        await engine.dispose()


async def main():
    print(f"=== Publishing nexplane-agent {VERSION} to s3://{BUCKET} ===")

    if not os.path.exists(BINARY_PATH):
        print(f"ERROR: binary not found at {BINARY_PATH}")
        sys.exit(1)

    size_mb = os.path.getsize(BINARY_PATH) // 1024 // 1024
    print(f"Binary: {BINARY_PATH} ({size_mb}MB)")

    print("Fetching AWS credentials from Nexplane connector...")
    creds = await get_creds()
    key_id = creds["access_key_id"]
    print(f"  access_key_id: {key_id[:8]}...  region: {creds.get('region', 'us-east-1')}")

    import boto3
    s3 = boto3.client(
        "s3",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
        aws_session_token=creds.get("session_token") or None,
    )

    binary_key = f"nexplane-agent-linux-amd64-{VERSION}"
    print(f"Uploading → s3://{BUCKET}/{binary_key} ...")
    with open(BINARY_PATH, "rb") as f:
        s3.put_object(
            Bucket=BUCKET,
            Key=binary_key,
            Body=f,
                        ContentType="application/octet-stream",
        )
    print("  ✓ Binary uploaded")

    print(f"Updating version file → {VERSION} ...")
    s3.put_object(
        Bucket=BUCKET,
        Key="version",
        Body=VERSION.encode(),
                ContentType="text/plain",
    )
    print("  ✓ Version file updated")

    print()
    url = f"https://{BUCKET}.s3.us-east-1.amazonaws.com/{binary_key}"
    print(f"Published: {url}")
    print(f"Version:   https://{BUCKET}.s3.us-east-1.amazonaws.com/version")


asyncio.run(main())
