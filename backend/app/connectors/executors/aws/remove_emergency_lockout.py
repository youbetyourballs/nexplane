# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Removes the nexplane-emergency-lockout IAM inline policy from a user."""
import os
import boto3
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    user = (
        parameters.get("user")
        or parameters.get("user_identifier")
        or parameters.get("user_name", "")
    )
    if not user:
        return {"rolled_back": False, "reason": "no user_identifier in parameters"}

    creds = (connector.credentials if connector else None) or {}
    # Fall back through stored credentials then env vars
    if not creds.get("access_key_id"):
        creds = parameters.get("_aws_creds") or {}
    if not creds.get("access_key_id"):
        key = os.environ.get("AWS_ACCESS_KEY_ID")
        secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
        if key and secret:
            creds = {"access_key_id": key, "secret_access_key": secret,
                     "region": os.environ.get("AWS_DEFAULT_REGION", "us-east-1")}

    iam = boto3.Session(
        aws_access_key_id=creds.get("access_key_id") or None,
        aws_secret_access_key=creds.get("secret_access_key") or None,
        region_name=creds.get("region", "us-east-1"),
    ).client("iam")

    try:
        iam.delete_user_policy(UserName=user, PolicyName="nexplane-emergency-lockout")
        status = "unlocked"
    except iam.exceptions.NoSuchEntityException:
        status = "policy_not_found"  # Already removed — treat as success
    except Exception as e:
        return {"rolled_back": False, "reason": str(e), "user": user}

    return {
        "action": "remove_emergency_lockout",
        "user": user,
        "status": status,
        "unlocked_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    # Rollback of an unlock = re-lock — not normally needed
    return {"rolled_back": False, "reason": "re-locking not supported via rollback"}
