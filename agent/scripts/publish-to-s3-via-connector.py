#!/usr/bin/env python3
"""Publish the Nexplane agent binary to S3 using credentials from the Nexplane AWS connector.

Run from inside the backend container:
  docker compose exec backend python /app/../agent/scripts/publish-to-s3-via-connector.py [VERSION]

Or from the host:
  docker compose exec -e PYTHONPATH=/app backend \
    python /path/to/publish-to-s3-via-connector.py 0.2.0
"""
import asyncio
import os
import sys

BUCKET = "nexplane-agent-downloads"
BINARY_PATH = os.path.join(os.path.dirname(__file__), "..", "dist", "nexplane-agent-linux-amd64")
VERSION = sys.argv[1] if len(sys.argv) > 1 else "0.2.0"


async def get_aws_creds() -> dict:
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
            result = await db.execute(
                sa.select(Connector).where(Connector.connector_type == ConnectorType.aws)
            )
            conn = result.scalars().first()
            if not conn:
                raise RuntimeError("No AWS connector found in Nexplane database")
            await _attach_credentials(conn, db)
            creds = getattr(conn, "credentials", {}) or {}
            if not creds.get("access_key_id"):
                raise RuntimeError("AWS connector has no access_key_id in credentials")
            return creds
    finally:
        await engine.dispose()


async def main():
    print(f"=== Nexplane Agent Publisher (via AWS connector) ===")
    print(f"Version : {VERSION}")
    print(f"Bucket  : s3://{BUCKET}")
    print(f"Binary  : {BINARY_PATH}")
    print()

    if not os.path.exists(BINARY_PATH):
        print(f"ERROR: Binary not found at {BINARY_PATH}")
        print("Build it first: cd agent && GOOS=linux GOARCH=amd64 go build -o dist/nexplane-agent-linux-amd64 ./")
        sys.exit(1)

    print("Fetching AWS credentials from Nexplane connector...")
    creds = await get_aws_creds()
    region = creds.get("region", "us-east-1")
    print(f"  Using access_key_id: {creds['access_key_id'][:8]}... region={region}")

    import boto3
    s3 = boto3.client(
        "s3",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=region,
        aws_session_token=creds.get("session_token") or None,
    )

    binary_key = f"nexplane-agent-linux-amd64-{VERSION}"
    binary_size = os.path.getsize(BINARY_PATH)
    print(f"Uploading {binary_size // 1024 // 1024}MB → s3://{BUCKET}/{binary_key} ...")

    with open(BINARY_PATH, "rb") as f:
        s3.put_object(
            Bucket=BUCKET,
            Key=binary_key,
            Body=f,
            ACL="public-read",
            ContentType="application/octet-stream",
        )
    print(f"  ✓ Binary uploaded")

    print(f"Updating version pointer → {VERSION} ...")
    s3.put_object(
        Bucket=BUCKET,
        Key="version",
        Body=VERSION.encode(),
        ACL="public-read",
        ContentType="text/plain",
    )
    print(f"  ✓ Version file updated")

    print()
    print(f"=== Published version {VERSION} ===")
    print(f"Binary: https://{BUCKET}.s3.us-east-1.amazonaws.com/{binary_key}")
    print(f"Version: https://{BUCKET}.s3.us-east-1.amazonaws.com/version")


asyncio.run(main())
