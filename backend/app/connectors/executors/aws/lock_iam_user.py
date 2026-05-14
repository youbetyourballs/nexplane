import os
import boto3
from datetime import datetime, timezone


def _iam_client(connector, execution_result: dict | None = None):
    """Build IAM client, falling back through multiple credential sources."""
    creds = (connector.credentials if connector else None) or {}

    # If connector has no credentials, try execution_result (stored during execute)
    if not creds.get("access_key_id") and execution_result:
        stored = execution_result.get("_aws_creds") or {}
        creds = stored or creds

    # Last resort: explicit env vars (set when running smoke tests via docker compose exec -e)
    if not creds.get("access_key_id"):
        key = os.environ.get("AWS_ACCESS_KEY_ID")
        secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
        if key and secret:
            creds = {
                "access_key_id": key,
                "secret_access_key": secret,
                "region": os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
            }

    return boto3.Session(
        aws_access_key_id=creds.get("access_key_id") or None,
        aws_secret_access_key=creds.get("secret_access_key") or None,
        region_name=creds.get("region", "us-east-1"),
    ).client("iam")


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    user = parameters.get("user_name") or parameters.get("user_identifier", "")
    if not user:
        raise ValueError("user_name or user_identifier required")
    iam = _iam_client(connector)
    policy_name = "nexplane-emergency-lockout"
    deny_policy = '{"Version":"2012-10-17","Statement":[{"Effect":"Deny","Action":"*","Resource":"*"}]}'
    iam.put_user_policy(UserName=user, PolicyName=policy_name, PolicyDocument=deny_policy)

    # Store credentials so rollback can use them even when connector=None
    creds = (connector.credentials if connector else None) or {}
    if not creds.get("access_key_id"):
        key = os.environ.get("AWS_ACCESS_KEY_ID")
        secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
        if key and secret:
            creds = {"access_key_id": key, "secret_access_key": secret,
                     "region": os.environ.get("AWS_DEFAULT_REGION", "us-east-1")}

    return {
        "action": "lock_iam_user", "user": user, "status": "locked",
        "policy_name": policy_name,
        "locked_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
        "_aws_creds": creds,  # persisted so rollback can use same account
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    user = execution_result.get("user") or parameters.get("user_identifier", "")
    if not user:
        return {"rolled_back": False, "reason": "no user found in parameters or execution_result"}
    iam = _iam_client(connector, execution_result)
    try:
        iam.delete_user_policy(UserName=user, PolicyName="nexplane-emergency-lockout")
    except Exception as e:
        return {"rolled_back": False, "reason": str(e)}
    return {"rolled_back": True, "user": user}
