# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import boto3
import json
import os
from datetime import datetime, timezone


def _iam_client(connector, execution_result: dict | None = None):
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
    ).client("iam")

DENY_WRITE_POLICY = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Deny",
        "Action": [
            "ec2:RunInstances", "ec2:TerminateInstances", "ec2:StopInstances",
            "ec2:StartInstances", "ec2:CreateSecurityGroup", "ec2:DeleteSecurityGroup",
            "ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress",
            "s3:PutObject", "s3:DeleteObject", "s3:CreateBucket", "s3:DeleteBucket",
            "iam:CreateUser", "iam:DeleteUser", "iam:AttachUserPolicy", "iam:DetachUserPolicy",
            "iam:PutUserPolicy", "iam:DeleteUserPolicy",
            "rds:CreateDBInstance", "rds:DeleteDBInstance", "rds:ModifyDBInstance",
            "cloudformation:CreateStack", "cloudformation:DeleteStack", "cloudformation:UpdateStack"
        ],
        "Resource": "*"
    }]
})

MFA_REQUIRED_POLICY = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Deny", "Action": "*", "Resource": "*",
        "Condition": {"BoolIfExists": {"aws:MultiFactorAuthPresent": "false"}}
    }]
})


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    user = parameters.get("user_name") or parameters.get("user_identifier", "")
    mode = parameters.get("mode", "demote_to_readonly")
    iam = _iam_client(connector)
    creds = (connector.credentials if connector else None) or {}
    if not creds.get("access_key_id"):
        key = os.environ.get("AWS_ACCESS_KEY_ID")
        secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
        if key and secret:
            creds = {"access_key_id": key, "secret_access_key": secret,
                     "region": os.environ.get("AWS_DEFAULT_REGION", "us-east-1")}

    if mode == "demote_to_readonly":
        policy_name = "nexplane-scope-reduction-deny-write"
        iam.put_user_policy(UserName=user, PolicyName=policy_name, PolicyDocument=DENY_WRITE_POLICY)
    elif mode == "mfa_required":
        policy_name = "nexplane-scope-reduction-mfa-required"
        iam.put_user_policy(UserName=user, PolicyName=policy_name, PolicyDocument=MFA_REQUIRED_POLICY)
    elif mode == "ip_restriction":
        cidr = parameters.get("allowed_cidr", "10.0.0.0/8")
        policy_name = "nexplane-scope-reduction-ip-restriction"
        policy = json.dumps({"Version": "2012-10-17", "Statement": [{
            "Effect": "Deny", "Action": "*", "Resource": "*",
            "Condition": {"NotIpAddress": {"aws:SourceIp": [cidr]}}
        }]})
        iam.put_user_policy(UserName=user, PolicyName=policy_name, PolicyDocument=policy)
    else:
        raise ValueError(f"Unknown mode: {mode}")

    return {
        "action": "user_scope_reduction", "user": user, "mode": mode,
        "policy_name": policy_name,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
        "_aws_creds": creds,  # persisted for rollback
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    user = execution_result.get("user") or parameters.get("user_identifier", "")
    policy_name = execution_result.get("policy_name", "nexplane-scope-reduction-deny-write")
    iam = _iam_client(connector, execution_result)
    try:
        iam.delete_user_policy(UserName=user, PolicyName=policy_name)
    except Exception as e:
        return {"rolled_back": False, "reason": str(e)}
    return {"rolled_back": True}
