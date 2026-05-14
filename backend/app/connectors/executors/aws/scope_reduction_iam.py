import boto3
import json
from datetime import datetime, timezone

DENY_WRITE_POLICY = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Deny",
        "Action": ["*:Put*", "*:Create*", "*:Delete*", "*:Update*", "*:Modify*",
                   "*:Attach*", "*:Detach*", "*:Start*", "*:Stop*", "*:Terminate*"],
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
    creds = (connector.credentials if connector else None) or {}
    iam = boto3.Session(
        aws_access_key_id=creds.get("access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key"),
        region_name=creds.get("region", "us-east-1"),
    ).client("iam")

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
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    user = execution_result.get("user") or parameters.get("user_identifier", "")
    policy_name = execution_result.get("policy_name", "nexplane-scope-reduction-deny-write")
    creds = (connector.credentials if connector else None) or {}
    iam = boto3.Session(
        aws_access_key_id=creds.get("access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key"),
        region_name=creds.get("region", "us-east-1"),
    ).client("iam")
    try:
        iam.delete_user_policy(UserName=user, PolicyName=policy_name)
    except Exception as e:
        return {"rolled_back": False, "reason": str(e)}
    return {"rolled_back": True}
