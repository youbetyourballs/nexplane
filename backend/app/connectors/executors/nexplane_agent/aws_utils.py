# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import logging

logger = logging.getLogger(__name__)


def _ec2_client(creds: dict):
    import boto3
    return boto3.client(
        "ec2",
        region_name=creds.get("region", creds.get("aws_region", "us-east-1")),
        aws_access_key_id=creds.get("access_key_id", creds.get("aws_access_key_id")),
        aws_secret_access_key=creds.get("secret_access_key", creds.get("aws_secret_access_key")),
        aws_session_token=creds.get("session_token", creds.get("aws_session_token")),
    )


def _s3_client(creds: dict):
    import boto3
    return boto3.client(
        "s3",
        region_name=creds.get("region", creds.get("aws_region", "us-east-1")),
        aws_access_key_id=creds.get("access_key_id", creds.get("aws_access_key_id")),
        aws_secret_access_key=creds.get("secret_access_key", creds.get("aws_secret_access_key")),
        aws_session_token=creds.get("session_token", creds.get("aws_session_token")),
    )


async def _load_aws_creds(aws_connector_id: str, fallback_connector) -> dict:
    creds = getattr(fallback_connector, "credentials", {}) or {}
    if aws_connector_id:
        try:
            import uuid as _uuid
            from app.database import AsyncSessionLocal
            from app.models.connector import Connector as _Connector
            from app.services.connector_service import _attach_credentials
            async with AsyncSessionLocal() as db:
                conn = await db.get(_Connector, _uuid.UUID(aws_connector_id))
                if conn:
                    await _attach_credentials(conn, db)
                    creds = conn.credentials or {}
        except Exception as exc:
            logger.warning("Could not load AWS connector %s: %s", aws_connector_id, exc)
    return creds
