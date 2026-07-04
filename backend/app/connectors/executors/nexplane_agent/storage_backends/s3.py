# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


def _client(config: dict):
    import boto3
    return boto3.client(
        "s3",
        region_name=config.get("region", "us-east-1"),
        aws_access_key_id=config.get("aws_access_key_id"),
        aws_secret_access_key=config.get("aws_secret_access_key"),
        aws_session_token=config.get("aws_session_token"),
    )


async def put(key: str, data: bytes, config: dict) -> str:
    """Upload bytes to S3. Returns s3://bucket/key URI."""
    bucket = config["bucket"]

    def _sync():
        s3 = _client(config)
        s3.put_object(Bucket=bucket, Key=key, Body=data)
        return f"s3://{bucket}/{key}"

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync)


async def put_file(key: str, local_path: str, config: dict) -> str:
    """Upload a local file to S3. Returns s3://bucket/key URI."""
    bucket = config["bucket"]

    def _sync():
        s3 = _client(config)
        s3.upload_file(local_path, bucket, key)
        return f"s3://{bucket}/{key}"

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync)


async def delete_prefix(prefix: str, config: dict) -> dict:
    """Delete all objects under prefix. Returns {deleted_count}."""
    bucket = config["bucket"]

    def _sync():
        s3 = _client(config)
        deleted = 0
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objects})
                deleted += len(objects)
        return {"deleted_count": deleted}

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync)


async def delete(uri: str, config: dict) -> None:
    """Delete a single S3 object by its s3://bucket/key URI."""
    parts = uri.replace("s3://", "").split("/", 1)
    bucket, key = parts[0], parts[1] if len(parts) > 1 else ""

    def _sync():
        s3 = _client(config)
        s3.delete_object(Bucket=bucket, Key=key)

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        await loop.run_in_executor(pool, _sync)
