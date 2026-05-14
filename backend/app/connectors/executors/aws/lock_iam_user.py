import boto3
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    user = parameters.get("user_name") or parameters.get("user_identifier", "")
    if not user:
        raise ValueError("user_name or user_identifier required")
    creds = (connector.credentials if connector else None) or {}
    iam = boto3.Session(
        aws_access_key_id=creds.get("access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key"),
        region_name=creds.get("region", "us-east-1"),
    ).client("iam")
    policy_name = "nexplane-emergency-lockout"
    deny_policy = '{"Version":"2012-10-17","Statement":[{"Effect":"Deny","Action":"*","Resource":"*"}]}'
    iam.put_user_policy(UserName=user, PolicyName=policy_name, PolicyDocument=deny_policy)
    return {
        "action": "lock_iam_user", "user": user, "status": "locked",
        "policy_name": policy_name,
        "locked_at": datetime.now(timezone.utc).isoformat(),
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
        iam.delete_user_policy(UserName=user, PolicyName="nexplane-emergency-lockout")
    except Exception as e:
        return {"rolled_back": False, "reason": str(e)}
    return {"rolled_back": True, "user": user}
