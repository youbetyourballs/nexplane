# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""AWS IAM access key rotation connector action."""

import boto3
from typing import Any


async def create_iam_access_key(connector_config: Any, iam_username: str) -> dict:
    """
    Creates a new IAM access key for iam_username.
    Returns {"new_api_key": "AKIA...:secret"} — the colon-separated ID:secret.
    Never logs the secret portion.
    """
    client = boto3.client("iam", **connector_config.boto_kwargs())
    response = client.create_access_key(UserName=iam_username)
    key = response["AccessKey"]
    return {
        "new_api_key": f"{key['AccessKeyId']}:{key['SecretAccessKey']}",
    }


async def delete_iam_access_key(connector_config: Any, iam_username: str, key_id: str) -> None:
    """Permanently deletes the IAM access key identified by key_id."""
    client = boto3.client("iam", **connector_config.boto_kwargs())
    client.delete_access_key(UserName=iam_username, AccessKeyId=key_id)
