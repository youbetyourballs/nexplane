import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    username = parameters.get('username', 'nexplane-user')
    tags = [{"Key": "ManagedBy", "Value": "nexplane"}]

    if not creds:
        return {
            "action": "create_iam_user",
            "username": username,
            "access_key_id": "AKIAMOCKKEY0000001",
            "mock": True,
            "_auto_asset": {
                "name": username,
                "asset_type": "identity",
                "environment": "prod",
                "criticality": "high",
                "asset_metadata": {"username": username, "provider": "aws"},
                "tags": ["iam", "nexplane-managed"],
            },
        }

    from ._client import get_boto3_client
    iam = get_boto3_client(creds, 'iam')
    loop = asyncio.get_event_loop()

    def _call():
        iam.create_user(UserName=username, Tags=tags)
        key_resp = iam.create_access_key(UserName=username)
        return key_resp['AccessKey']

    key = await loop.run_in_executor(None, _call)
    return {
        "action": "create_iam_user",
        "username": username,
        "access_key_id": key['AccessKeyId'],
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": {
            "name": username,
            "asset_type": "identity",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "username": username,
                "access_key_id": key['AccessKeyId'],
                "region": creds.get('region', 'us-east-1'),
                "provider": "aws",
            },
            "tags": ["iam", "nexplane-managed"],
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.delete_iam_user import execute as delete
    return await delete({"username": execution_result.get('username', parameters.get('username'))}, [], connector)
