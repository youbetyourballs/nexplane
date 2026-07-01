# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import boto3
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    user = parameters.get("user_name") or parameters.get("user_identifier", "")
    creds = (connector.credentials if connector else None) or {}
    iam = boto3.Session(
        aws_access_key_id=creds.get("access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key"),
        region_name=creds.get("region", "us-east-1"),
    ).client("iam")
    policy_name = "nexplane-enforce-mfa-required"
    mfa_policy = '{"Version":"2012-10-17","Statement":[{"Sid":"DenyWithoutMFA","Effect":"Deny","Action":"*","Resource":"*","Condition":{"BoolIfExists":{"aws:MultiFactorAuthPresent":"false"}}}]}'
    iam.put_user_policy(UserName=user, PolicyName=policy_name, PolicyDocument=mfa_policy)
    return {
        "action": "enforce_mfa", "user": user, "status": "mfa_required",
        "policy_name": policy_name,
        "enforced_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    user = execution_result.get("user") or parameters.get("user_identifier", "")
    creds = (connector.credentials if connector else None) or {}
    iam = boto3.Session(
        aws_access_key_id=creds.get("access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key"),
        region_name=creds.get("region", "us-east-1"),
    ).client("iam")
    try:
        iam.delete_user_policy(UserName=user, PolicyName="nexplane-enforce-mfa-required")
    except Exception as e:
        return {"rolled_back": False, "reason": str(e)}
    return {"rolled_back": True}
