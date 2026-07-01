# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import os
import json
import boto3
import secrets
import string
from datetime import datetime, timezone


def _sm_client(connector, execution_result=None):
    creds = (connector.credentials if connector else None) or {}
    if not creds.get("access_key_id") and execution_result:
        creds = execution_result.get("_aws_creds") or creds
    if not creds.get("access_key_id"):
        key = os.environ.get("AWS_ACCESS_KEY_ID")
        secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
        if key and secret:
            creds = {"access_key_id": key, "secret_access_key": secret,
                     "region": os.environ.get("AWS_DEFAULT_REGION", "us-east-1")}
    return boto3.Session(
        aws_access_key_id=creds.get("access_key_id") or None,
        aws_secret_access_key=creds.get("secret_access_key") or None,
        region_name=creds.get("region", "us-east-1"),
    ).client("secretsmanager")


def _generate_secret_value(length: int = 32) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%^&*()"
    return "".join(secrets.choice(chars) for _ in range(length))


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    secret_id = parameters.get("secret_id") or parameters.get("secret_name", "")
    if not secret_id:
        raise ValueError("secret_id is required")

    sm = _sm_client(connector)
    creds = (connector.credentials if connector else None) or {}
    if not creds.get("access_key_id"):
        key = os.environ.get("AWS_ACCESS_KEY_ID")
        if key:
            creds = {"access_key_id": key, "secret_access_key": os.environ.get("AWS_SECRET_ACCESS_KEY"),
                     "region": os.environ.get("AWS_DEFAULT_REGION", "us-east-1")}

    # Get current secret value (for rollback reference)
    try:
        current = sm.get_secret_value(SecretId=secret_id)
        old_version = current.get("VersionId")
    except Exception:
        old_version = None

    # Generate new secret value
    new_secret_type = parameters.get("secret_type", "string")
    if new_secret_type == "json":
        # Rotate the 'password' field in a JSON secret
        try:
            old_data = json.loads(current.get("SecretString", "{}"))
        except Exception:
            old_data = {}
        new_data = {**old_data, "password": _generate_secret_value(24)}
        new_value = json.dumps(new_data)
    else:
        new_value = parameters.get("new_value") or _generate_secret_value(32)

    sm.put_secret_value(SecretId=secret_id, SecretString=new_value)

    return {
        "action": "rotate_secrets_manager_secret",
        "secret_id": secret_id,
        "old_version": old_version,
        "rotated_at": datetime.now(timezone.utc).isoformat(),
        "_aws_creds": creds,
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    secret_id = parameters.get("secret_id") or parameters.get("secret_name", "")
    old_version = execution_result.get("old_version")
    if not old_version:
        return {"rolled_back": False, "reason": "no old_version to restore"}
    sm = _sm_client(connector, execution_result)
    try:
        # Restore the old version
        sm.update_secret_version_stage(
            SecretId=secret_id,
            VersionStage="AWSCURRENT",
            MoveToVersionId=old_version,
        )
        return {"rolled_back": True, "restored_version": old_version}
    except Exception as e:
        return {"rolled_back": False, "reason": str(e)}
