# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""IR forensics service — pre-signed URL generation and bundle manifest persistence."""
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.forensic_bundle import ForensicBundle


def generate_upload_url(bundle_id: uuid.UUID, destination: str) -> str:
    """
    Generate a pre-signed S3 PUT URL for uploading the forensic bundle.
    destination is e.g. 's3://nexplane-forensics/bundles/'.
    Returns the pre-signed URL as a string.

    Note: Requires boto3 and AWS credentials configured in the environment.
    Falls back to a placeholder URL if boto3 is not available.
    """
    dest = destination.rstrip("/")
    assert dest.startswith("s3://"), f"upload_destination must start with s3://, got: {dest}"
    path = dest[5:]
    bucket, _, prefix = path.partition("/")
    key = f"{prefix}/{bundle_id}.tar.gz" if prefix else f"{bundle_id}.tar.gz"

    try:
        import boto3
        from botocore.config import Config
        s3 = boto3.client(
            "s3",
            region_name="us-east-1",
            config=Config(signature_version="s3v4"),
        )
        url = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key, "ContentType": "application/gzip"},
            ExpiresIn=3600,
        )
        return url
    except Exception:
        # Return a placeholder when S3 is not configured
        return f"https://{bucket}.s3.amazonaws.com/{key}"


async def store_bundle_manifest(
    db: AsyncSession,
    asset_id: uuid.UUID,
    change_request_id: uuid.UUID,
    upload_url: str,
    manifest: dict,
    size_bytes: int | None,
) -> ForensicBundle:
    """Persist a forensic bundle manifest to the database."""
    bundle = ForensicBundle(
        asset_id=asset_id,
        change_request_id=change_request_id,
        upload_url=upload_url,
        manifest=manifest,
        size_bytes=size_bytes,
        collected_at=datetime.now(timezone.utc),
    )
    db.add(bundle)
    await db.flush()
    await db.refresh(bundle)
    return bundle
