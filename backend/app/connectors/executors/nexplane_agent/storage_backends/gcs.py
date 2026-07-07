# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""GCS storage backend. URI scheme: gcs://bucket/key."""
import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

_NAME = "gcs"


def _client(config: dict):
    from google.cloud import storage
    key_json = config.get("service_account_key_json")
    if key_json:
        if isinstance(key_json, str):
            key_json = json.loads(key_json)
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_info(
            key_json,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        return storage.Client(credentials=creds, project=key_json.get("project_id"))
    # Fall back to ADC for local dev / workload identity
    return storage.Client()


def _require_bucket(config: dict) -> str:
    bucket = config.get("bucket")
    if not bucket:
        raise ValueError("GCS storage backend requires config['bucket'] to be set")
    return bucket


async def upload(local_path: str, dest_key: str, config: dict) -> str:
    """Upload a local file to GCS. Returns gcs://bucket/key URI."""
    bucket_name = _require_bucket(config)

    def _sync():
        client = _client(config)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(dest_key)
        blob.upload_from_filename(local_path)
        return f"gcs://{bucket_name}/{dest_key}"

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync)


async def download(uri: str, local_path: str, config: dict) -> None:
    """Download a GCS object by its gcs://bucket/key URI to local_path."""
    _require_bucket(config)
    parts = uri.replace("gcs://", "").split("/", 1)
    bucket_name, key = parts[0], parts[1] if len(parts) > 1 else ""

    def _sync():
        client = _client(config)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(key)
        blob.download_to_filename(local_path)

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        await loop.run_in_executor(pool, _sync)


async def delete(uri: str, config: dict) -> None:
    """Delete a single GCS object by its gcs://bucket/key URI."""
    _require_bucket(config)
    parts = uri.replace("gcs://", "").split("/", 1)
    bucket_name, key = parts[0], parts[1] if len(parts) > 1 else ""

    def _sync():
        client = _client(config)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(key)
        blob.delete()

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        await loop.run_in_executor(pool, _sync)


async def put_bytes(key: str, data: bytes, config: dict) -> str:
    """Put raw bytes at key in GCS. Returns gcs://bucket/key URI."""
    bucket_name = _require_bucket(config)

    def _sync():
        client = _client(config)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(key)
        blob.upload_from_string(data)
        return f"gcs://{bucket_name}/{key}"

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync)


async def delete_prefix(prefix: str, config: dict) -> dict:
    """Delete all blobs under prefix. Returns {deleted_count: N}."""
    bucket_name = _require_bucket(config)

    def _sync():
        client = _client(config)
        bucket = client.bucket(bucket_name)
        blobs = list(bucket.list_blobs(prefix=prefix))
        for blob in blobs:
            blob.delete()
        return {"deleted_count": len(blobs)}

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync)


async def list_prefix(prefix: str, config: dict) -> list:
    """List all blobs under prefix. Returns list of gcs://bucket/key URIs."""
    bucket_name = _require_bucket(config)

    def _sync():
        client = _client(config)
        bucket = client.bucket(bucket_name)
        blobs = bucket.list_blobs(prefix=prefix)
        return [f"gcs://{bucket_name}/{blob.name}" for blob in blobs]

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync)
